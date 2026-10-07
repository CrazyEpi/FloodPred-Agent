"""Two standalone read-only tools. Responses remain UNVERIFIED candidates.

No proxy, cookies, JS, redirects, retries, local writes, or arbitrary headers.
DNS is validated and a validated IP is pinned through TCP + hostname-verified TLS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
from queue import Queue, Empty
import re
import socket
import ssl
from threading import Thread
import time
from urllib.parse import urlsplit, urlunsplit, urlencode

from .web_policy import WebDecision, search_terms, TOPIC_DOMAINS

BRAVE_HOST = "api.search.brave.com"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class WebToolError(ValueError):
    """Safe messages only: never echo URLs, response bodies or credentials."""


@dataclass
class NetworkBudget:
    max_requests: int = 4
    max_seconds: float = 20
    calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 4:
            raise WebToolError("网络请求上限须为 1–4")
        if not 0 < self.max_seconds <= 20:
            raise WebToolError("网络时间预算须为 0–20 秒")

    def remaining(self):
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise WebToolError("network_time_limit：网络时间预算已耗尽")
        return remaining

    def reserve(self):
        self.remaining()
        if self.calls >= self.max_requests:
            raise WebToolError("network_call_limit：网络调用预算已耗尽")
        self.calls += 1


def validate_url(url: str, allowed_hosts: tuple[str, ...]) -> tuple[str, str]:
    if not isinstance(url, str) or not 1 <= len(url) <= 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise WebToolError("URL 格式无效")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or host != host.encode("idna").decode("ascii")
            or host.endswith(".") or host not in allowed_hosts):
            raise WebToolError("仅允许登记域名的 HTTPS 443 URL，无认证信息")
        # IP literals cannot pass even if someone accidentally adds one to config.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise WebToolError("禁止 IP 地址 URL")
        return host, urlunsplit(("https", host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise WebToolError("URL 未通过安全检查") from exc


def resolve_public(host: str, timeout: float):
    output = Queue(maxsize=1)
    def resolve():
        try:
            output.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            output.put(None)
    Thread(target=resolve, daemon=True).start()
    try:
        records = output.get(timeout=timeout)
    except Empty as exc:
        raise WebToolError("DNS 解析超时") from exc
    if not records:
        raise WebToolError("DNS 解析失败")
    for family, _, _, _, address in records:
        ip = ipaddress.ip_address(address[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if (family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global
            or ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
            or (mapped is not None and not mapped.is_global)
            or getattr(ip, "teredo", None) is not None
            or getattr(ip, "sixtofour", None) is not None):
            raise WebToolError("DNS 包含非公网或隧道地址，拒绝访问")
    return records[0][0], records[0][4]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, family, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address, self.family = address, family

    def connect(self):
        sock = socket.socket(self.family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.address)  # No second DNS lookup.
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(self.address[0]):
                raise WebToolError("连接地址不符合已核验 DNS")
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


@dataclass(frozen=True)
class RawResponse:
    body: bytes
    content_type: str
    last_modified: str | None


class SafeTransport:
    def get(self, url: str, *, allowed_hosts: tuple[str, ...], budget: NetworkBudget,
            accept: str, credential: str | None = None) -> RawResponse:
        host, clean = validate_url(url, allowed_hosts)
        if credential is not None and (host != BRAVE_HOST or urlsplit(clean).path != "/res/v1/web/search"):
            raise WebToolError("认证凭据只能发往固定搜索服务接口")
        budget.reserve()
        connection = None
        try:
            family, address = resolve_public(host, min(6.0, budget.remaining()))
            connection = _PinnedHTTPS(host, address, family, min(8.0, budget.remaining()))
            parts = urlsplit(clean)
            headers = {"User-Agent": "FloodPred/1.0 (public-evidence-preview)",
                       "Accept": accept, "Accept-Encoding": "identity"}
            if credential is not None:
                headers["X-Subscription-Token"] = credential
            connection.request("GET", parts.path + ("?" + parts.query if parts.query else ""), headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise WebToolError("redirect_blocked：第一版不跟随重定向")
            if response.status != 200:
                raise WebToolError(f"公开资料请求失败：HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            mime = content_type.split(";", 1)[0].strip()
            permitted = ("application/json",) if accept == "application/json" else ("text/html", "application/xhtml+xml")
            if mime not in permitted:
                raise WebToolError("unsupported_content：只接受所请求的 JSON/HTML，不下载文件")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise WebToolError("拒绝压缩响应，避免解压体积绕过限制")
            max_bytes = 1_000_000
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise WebToolError("response_too_large：响应体积超过限制")
            body = bytearray()
            while True:
                remaining = budget.remaining()
                if connection.sock:
                    connection.sock.settimeout(min(8.0, remaining))
                chunk = response.read1(min(65536, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise WebToolError("response_too_large：响应体积超过限制")
            budget.remaining()
            return RawResponse(bytes(body), content_type, response.getheader("Last-Modified"))
        except WebToolError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise WebToolError(f"网络读取失败：{type(exc).__name__}，未展示请求或正文") from exc
        finally:
            if connection is not None:
                connection.close()


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    provider_date: str | None
    status: str = "unverified_search_candidate"


@dataclass(frozen=True)
class PagePreview:
    url: str
    title: str
    paragraphs: tuple[str, ...]
    fetched_at_utc: str
    raw_sha256: str
    last_modified_header: str | None
    published_at: str | None = None
    status: str = "unverified_page_preview"


class _HTMLText(HTMLParser):
    """Text preview only. Not a sanitizer, evidence verifier or injection detector."""
    SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "footer"}
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "li", "section", "article", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.title_parts = []
        self.parts = []
        self.paragraphs = []

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        if text:
            self.paragraphs.append(text[:5000])
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = (tag in self.SKIP or "hidden" in attributes
                  or attributes.get("aria-hidden", "").lower() == "true"
                  or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attributes.get("style", ""), re.I))
        if tag in self.BLOCK:
            self.flush()
        if tag not in {"br", "img", "meta", "link", "input", "hr", "wbr", "source"}:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(hidden for _, hidden in self.stack):
            return
        if any(tag == "title" for tag, _ in self.stack):
            self.title_parts.append(data)
        else:
            self.parts.append(data)


def _api_key() -> str | None:
    value = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not value:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8-sig").splitlines():
                name, separator, item = line.partition("=")
                if separator and name.strip() == "BRAVE_SEARCH_API_KEY":
                    value = item.strip().strip("\"'")
                    break
    if not value or value.startswith("<") or value in ("YOUR_BRAVE_SEARCH_API_KEY", "填入你的搜索密钥"):
        return None
    if re.search(r"[\s\x00-\x1f\x7f]", value):
        raise WebToolError("搜索凭据格式无效")
    return value


def search_public_web(fragment: str, *, decision: WebDecision, budget: NetworkBudget | None = None,
                      transport: SafeTransport | None = None) -> tuple[SearchHit, ...]:
    _authorize(decision)
    query = search_terms(fragment, decision)
    key = _api_key()
    if not key:
        raise WebToolError("search_not_configured：未配置 BRAVE_SEARCH_API_KEY；不会调用搜索服务")
    scoped = query + " (" + " OR ".join("site:" + domain for domain in decision.domains) + ")"
    url = BRAVE_ENDPOINT + "?" + urlencode({"q": scoped, "count": 3, "safesearch": "strict"})
    response = (transport or SafeTransport()).get(url, allowed_hosts=(BRAVE_HOST,), budget=budget or NetworkBudget(),
                                                accept="application/json", credential=key)
    try:
        items = json.loads(response.body)["web"]["results"]
        if not isinstance(items, list):
            raise ValueError()
        hits = []
        seen = set()
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            try:
                _, clean = validate_url(item.get("url"), decision.domains)
            except WebToolError:
                continue
            if clean in seen:
                continue
            seen.add(clean)
            hits.append(SearchHit(str(item.get("title", ""))[:300], clean,
                                  str(item.get("description", ""))[:2000],
                                  item.get("page_age") if isinstance(item.get("page_age"), str) else None))
            if len(hits) == 3:
                break
        return tuple(hits)
    except (ValueError, KeyError, TypeError) as exc:
        raise WebToolError("搜索响应格式不符合预期，未展示原始正文") from exc


def fetch_public_page(url: str, *, decision: WebDecision, budget: NetworkBudget | None = None,
                      transport: SafeTransport | None = None) -> PagePreview:
    _authorize(decision)
    _, clean = validate_url(url, decision.domains)
    response = (transport or SafeTransport()).get(clean, allowed_hosts=decision.domains, budget=budget or NetworkBudget(),
                                                accept="text/html,application/xhtml+xml")
    if len(response.body) > 1_000_000:
        raise WebToolError("response_too_large：响应体积超过限制")
    charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", response.content_type)
    try:
        text = response.body.decode(charset.group(1) if charset else "utf-8", errors="replace")
        parser = _HTMLText()
        parser.feed(text)
        parser.flush()
    except (LookupError, ValueError) as exc:
        raise WebToolError("网页编码或 HTML 解析失败") from exc
    paragraphs = tuple(parser.paragraphs[:100])
    if not paragraphs:
        raise WebToolError("empty_page：未找到可读取正文（可能需登录或执行脚本）")
    return PagePreview(clean, " ".join(parser.title_parts).strip()[:300], paragraphs,
                       datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                       hashlib.sha256(response.body).hexdigest(), response.last_modified)


def _authorize(decision):
    if (not isinstance(decision, WebDecision) or decision.allowed is not True
        or decision.status != "eligible" or decision.topic not in TOPIC_DOMAINS
        or decision.domains != TOPIC_DOMAINS[decision.topic]):
        raise WebToolError("联网策略未允许读取资料，或域名范围被改动")
