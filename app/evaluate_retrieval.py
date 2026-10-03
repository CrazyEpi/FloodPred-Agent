"""Six fixed raw-query recall checks; not an answer accuracy benchmark.

Print JSON only, do not write to knowledge, data, backups, or request logs.
The baseline is legacy keyword search ON THE RAW QUERY, not the old Agent.
"""

import json

from .hybrid_retrieval import hybrid_search, make_request
from .knowledge import KnowledgeError, search


CASES = (
    ("旧水位设备的安装点在哪里", "housemill_old_sensor"),
    ("House Mill以前测水的东西摆在哪里", "housemill_old_sensor"),
    ("论文中潮水和降雨怎么用于预测", "thesis_forecast_design"),
    ("志愿者想知道水会碰到木梁多久", "housemill_volunteer_need"),
    ("论文里为什么快涨水时会慢半拍", "thesis_rapid_rise"),
    ("项目内部Caution是什么意思", "internal_watch"),
)


def main() -> int:
    rows = []
    for question, expected in CASES:
        row = {"question": question, "expected_concept": expected}
        for mode in ("legacy_raw_keyword", "raw_hybrid"):
            try:
                hits = (search(question, semantic=False) if mode == "legacy_raw_keyword" else
                        hybrid_search(make_request(question)).hits)
                rank = next((index for index, hit in enumerate(hits, 1)
                             if any(record["concept"] == expected
                                    for record in (hit.record, *hit.equivalent_records))), None)
                row[mode] = {"expected_rank": rank, "found_in_top5": rank is not None and rank <= 5,
                             "top1_concept": hits[0].record["concept"] if hits else None,
                             "candidate_ids": [hit.record["id"] for hit in hits]}
            except KnowledgeError as exc:
                row[mode] = {"expected_rank": None, "found_in_top5": False,
                             "error_type": type(exc).__name__}
        rows.append(row)
    report = {
        "scope": "6 fixed raw-query candidate recall regressions, NOT answer accuracy or an old-Agent comparison",
        "query_count": len(rows), "top_k": 5, "uses_llm": False, "uses_supplementary_concepts": False,
        "found_in_top5": {mode: sum(row[mode]["found_in_top5"] for row in rows)
                          for mode in ("legacy_raw_keyword", "raw_hybrid")},
        "cases": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["found_in_top5"]["raw_hybrid"] != len(CASES))


if __name__ == "__main__":
    raise SystemExit(main())
