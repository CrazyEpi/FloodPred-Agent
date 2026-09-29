# D5 DeepSeek 真实调用验证补充记录

日期：2026-09-29  
范围：仅验证用户配置密钥后的 D5 两类知识问答；未修改 D1–D5 代码或任何备份。

## 结果

- 项目根目录 `.env` 存在，程序判定 key 已配置；**未读取到日志或报告，也未展示密钥内容**。
- `.venv-d4` 下运行全套本地测试：**16/16 通过**，含删除目录记录/原文文件时模型调用次数为 0 的拒答测试。
- 真实 DeepSeek 请求 1：`项目内部 Watch 是什么`，成功。返回内部 Watch 为 level 1、未来 24 小时预测最高水位 `4.20 m` 至低于 `4.43 m`，引用 `cloud_flood_server/README.md:13-20 § 风险等级`，并在已核验事实里注明**不是官方 Flood Alert**。
- 真实 DeepSeek 请求 2：`模型局限是什么`，成功。返回 Watch/Warning/Severe 阈值的不同实测目标点均为 0，不能证明这些事件的召回率；引用 `HIGH_WATER_EVALUATION_REPORT.md:71-78 § 风险阈值背景`。
- 两次输出都通过程序的引用 ID、逐字引文、数字和关键结论门禁；命令退出码均为 0。

首次在受限执行环境调用出现 `URLError`；获准网络访问后，同一程序命令成功。这不是源资料或 key 解析失败。没有进行批量调用、负载或费用测试。

## 用户复现

在 PowerShell 运行：

```powershell
cd C:\UCL\DissertationAgent
$env:PYTHONIOENCODING='utf-8'
.\.venv-d4\Scripts\python.exe -m unittest discover -s tests -q
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '项目内部 Watch 是什么'
.\.venv-d4\Scripts\python.exe -m app.rag_cli --question '模型局限是什么'
```

后两条是真实 API 请求，可能产生费用；请不要将 `.env`、真实 key 或含密钥的终端记录分享出去。已完成的两次调用不证明开放式问题都安全；当前只支持两类窄领域知识问答。
