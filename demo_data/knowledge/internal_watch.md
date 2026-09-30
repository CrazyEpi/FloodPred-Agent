## 风险等级

- `0 No risk`: 未来 24h 最高水位低于 `4.20m`，ESP32 显示 `#`。
- `1 Watch`: 最高水位 `4.20m <= level < 4.43m`，表示接近洪水线但还没到。
- `2 Warning`: 最高水位达到 `4.43m`，或连续超过 `4.43m` 达到 1 小时。
- `3 Severe`: 最高水位达到 `4.70m`，或第一次达到 `4.43m` 的 ETA 小于等于 2 小时，或连续超过 `4.43m` 达到 2 小时。

`eta_minutes` 和 `next_flood_utc` 只按真正洪水线 `4.43m` 计算。Watch 只给 `watch_eta_minutes`。
