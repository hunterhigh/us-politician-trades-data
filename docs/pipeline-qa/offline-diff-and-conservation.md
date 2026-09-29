# 离线流水线差异与行处置守恒检查

状态：只读 QA 工具；不会抓取来源、改写输入、写 canonical 数据或发布分支。

## 检查范围

工具比较两次运行的候选行文件（JSON、JSONL 或 CSV）和各自的 `pipeline-run-manifest/v1`。对每条记录要求 `source_id`、`document_id` 和稳定行 ID（`candidate_id`、`row_id` 或 `stable_row_id`）。匹配身份为这三项；`source_sha256` 是参与字段比较的内容。因此相同逻辑行遇到新文档哈希时显示为字段变化，候选 ID 若本身绑定文档版本则会显示为移除加新增。

报告逐字段保留前后值和字段是否存在，分别列出新增、移除、变化和重复身份。重复记录不会被静默去重：报告会列出冲突身份，退出状态标记检查失败。

对每一侧，工具先用 ledger validator 检查 manifest 内部文档计数、版本键、时间、路径和 `accounted_rows`。然后读取候选行实际的四种处置计数（`qualified`、`quarantined`、`excluded`、`unrecognized`），与 manifest 逐项核对；处置值缺失或未知会报告为未计入行。通过守恒只表示数量对得上，不表示抽取事实正确。

项目当前的前端验收基准是用户指定的 HTML 文件，其五个 canonical 投影数组为 `people`、`transactions`、`reported_holdings`、`security_market_data`、`source_health`。本工具不重新定义这些字段，也不推断前端业务规则；ledger 格式候选行会按 ledger v1 validator 检查结构，其他 JSON/CSV 行按通用稳定身份及处置字段比较。

## 运行

从仓库根目录运行（Python 3.11+，无新增依赖）：

```powershell
$env:PYTHONPATH = "backend/src"
python -m unison_snapshot.pipeline_qa `
  --old-rows path/to/old-candidates.json `
  --new-rows path/to/new-candidates.csv `
  --old-manifest path/to/old-run-manifest.json `
  --new-manifest path/to/new-run-manifest.json `
  --old-artifact-root path/to/old-run-artifacts `
  --new-artifact-root path/to/new-run-artifacts `
  --json-out path/to/diff-report.json
```

输入 JSON 可为对象数组或 `{ "rows": [...] }` / `{ "candidates": [...] }`；JSONL 每行一个对象。CSV 至少要包含身份列和 `disposition` 列，其他列作为普通字段比较。ledger v1 候选行中的嵌套对象仅支持 JSON/JSONL；CSV 适用于扁平字段。

如果传入两个 artifact root，工具会重新计算各自 manifest `outputs[].path` 对应文件的 SHA-256；缺文件、哈希不同或符号链接逃出产物根目录均使对应检查失败并返回 `1`。未传 root 时，机器报告会将该侧标记为 `not_checked`，不声称产物哈希已核验。控制台输出人类可读的守恒、产物哈希和变更摘要。`--json-out` 会写完整机器可读 JSON，包含逐行变化及旧、新记录。退出码：`0` 表示输入可比较且无重复身份、无守恒错误、无已执行的哈希检查失败；`1` 表示重复身份、守恒失败或已执行的哈希检查失败；`2` 表示输入格式、manifest 或 ledger 行结构无效。存在普通新增、移除、字段变化时仍返回 `0`，因为它们是供审阅的差异，不是计数错误。

## 稳定行身份

优先使用来源内稳定、跨解析版本不变的逻辑行 ID。不要用数组索引或易变的排序位置充当 `row_id`。如果 parser 生成的 `candidate_id` 含源文档哈希，改版文档会按旧身份移除、新身份新增呈现；要得到跨文件版本逐字段对比，应提供独立稳定的 `row_id`。

## 限制

- 工具不会判定财务数值、日期、人物身份、交易资格或官方来源真假。
- 它不会把 `qualified` 当成已发布或已被前端接受。
- 只有提供两侧 artifact root 才会读取或重算 outputs 哈希；自动化影子验收应始终传入固定 run 的产物目录。
- 文件输入按 UTF-8 读取；JSONL 错误会包含行号。大文件会整体载入内存。
- 变更本身不导致非零退出；发布前应由负责人员逐项审阅差异，并结合 HTML 字段/缺失值契约和 golden fixtures。

## 离线测试

```powershell
$env:PYTHONPATH = "backend/src"
python -m unittest discover -s backend/tests/pipeline_qa -v
```
