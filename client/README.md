# 公开 GitHub 消费端适配

这是在对方新版 `snapshot_repo.py` 尚未交付时，本项目实现的可运行适配器。它不改 `review-input/` 中的原前端文件。

读取顺序：GitHub API 将 `main` 固定为 40 位 commit → 从 `raw.githubusercontent.com` 按该 commit 读取 manifest、索引与内容分片 → 校验 SHA-256、demo 边界、依赖和引用完整性 → 交给固定哈希的原包 `process_snapshot.py`。

项目根目录运行示例：

```powershell
$env:GITHUB_TOKEN = gh auth token  # 可选，但建议设置以提高 GitHub API 限额
python client/fetch_snapshot.py dashboard --owner hunterhigh --repo us-politician-trades-data --output .local/production-dashboard.json
python review-input/us-politician-trades-watch/scripts/render_dashboard.py --input .local/production-dashboard.json --output .local/production-dashboard.html
```

脚本会自动定位仓库内的 `backend/src`，不要求用户设置 `PYTHONPATH`。公开仓库可以匿名读取；GitHub 的匿名 API 限额较低，在共享出口网络中可能出现 403，因此生产运行建议设置只读可用的 `GITHUB_TOKEN`。Token 只用于 GitHub API 的 commit 解析，不会发送给 raw 内容主机。owner/repo 是固定标识，不接受任意基础 URL。

相较对方代理版“manifest + board”两个 HTTP 请求，公开 GitHub 直读多一次 commit 解析，所以 dashboard 首次为三个请求。它换来的结果是首版不需要 Cloudflare，也不会在一次报告中混合两个提交。
