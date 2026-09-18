# House PTR 增量运行

增量任务以官方年度索引的 SHA-256 为批次依据，以每个 PTR 的稳定字段签名为文档检查点。检查点只保存运行状态，不代表交易已经解析、复核或获准发布。

## 生成或恢复工作队列

```powershell
$env:PYTHONPATH = "$PWD/backend/src"
python -m unison_snapshot plan-house-ptr-sync `
  --discovery .local/house-2026-discovery.json `
  --archive .local/official-archive `
  --checkpoint .local/house-2026-checkpoint.json `
  --output .local/house-2026-checkpoint.json
```

第一次运行时省略 `--checkpoint`。后续可以让输入和输出指向同一个文件；命令采用临时文件替换，写入中断不会留下半份 JSON。规划器会从内容寻址归档中恢复已经下载的文件，因此“PDF 已落盘、检查点尚未更新”不会导致数据丢失。

检查点中的 `queue` 包含所有 `pending` 文档，以及已经到达重试时间的 `failed` 文档。下载成功后，用归档命令输出的元数据登记结果：

```powershell
python -m unison_snapshot record-house-ptr-result `
  --checkpoint .local/house-2026-checkpoint.json `
  --document-id 20035420 `
  --status archived `
  --metadata .local/house-20035420-archive.json `
  --output .local/house-2026-checkpoint.json
```

下载失败时保留有界错误和尝试次数，并按 15 分钟起、最长 24 小时的指数退避重新进入后续队列：

```powershell
python -m unison_snapshot record-house-ptr-result `
  --checkpoint .local/house-2026-checkpoint.json `
  --document-id 20035420 `
  --status failed `
  --error "temporary upstream failure" `
  --output .local/house-2026-checkpoint.json
```

## 必须人工处理的异常

- `official_index_row_changed`：同一文档 ID 的官方索引字段发生变化，重新进入 pending，不能沿用旧签名的完成状态。
- `missing_from_latest_index`：旧索引中的文档从新索引消失，只记录异常，不自动删除归档或生产事实。
- `multiple_archived_versions`：同一索引记录存在多个匹配原件版本，不能自动选择。
- `archive_metadata_mismatch`：本地归档与最新索引的姓名、日期、URL 等稳定字段不一致。
- `archived_evidence_missing`：检查点声称已经归档，但原件或元数据已不存在。

生产仓库通过 `House source state` workflow 每 6 小时重新发现官方年度索引，从队列顺序处理有界批次，把官方索引 ZIP、原始 PDF 和元数据写到公开 `evidence` 分支，再把检查点写到 `state` 分支。每份 PDF 完成后立即记录结果；失败按检查点退避。任何异常都应阻止该文档继续解析，但不应删除上一版已经发布的快照。
