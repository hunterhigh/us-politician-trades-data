# 受审阅的规范输入

发布工作流只接受此目录下的 JSON，例如 `ingest/current.json`。这里存的是已经完成来源核验的五个规范数组，不是原始 PDF、抓取日志或密钥。

生产输入必须满足：

- `meta.is_demo` 明确为 `false`，`meta.data_cutoff_at` 为带时区时间；
- `people`、`transactions`、`reported_holdings`、`security_market_data`、`source_health` 均存在；
- 当前行情功能关闭，因此 `security_market_data` 必须为空；
- 交易和持仓必须为 `official_matched`，引用存在的人物，并有金额区间、申报 ID 和官方 HTTPS URL；
- House URL 只接受 `disclosures-clerk.house.gov`，Senate 只接受 `efdsearch.senate.gov`，OGE 只接受 `oge.gov`/`www.oge.gov`；
- 未解析、人工申请中、修订替代或来源失败记录留在审计层，不伪装成规范交易/持仓。

本目录目前没有正式输入，发布工作流因此不会意外产生一份空的“生产快照”。真实采集器完成前，可由受审阅 PR 放入候选文件，再手动运行发布工作流。
