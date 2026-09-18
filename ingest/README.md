# 受审阅的规范输入

发布工作流只接受此目录下的 JSON，例如 `ingest/current.json`。这里存的是已经完成来源核验的五个规范数组，不是原始 PDF、抓取日志或密钥。

生产输入必须满足：

- `meta.is_demo` 明确为 `false`，`meta.data_cutoff_at` 为带时区时间；
- `people`、`transactions`、`reported_holdings`、`security_market_data`、`source_health` 均存在；
- 当前行情功能关闭，因此 `security_market_data` 必须为空；
- 交易和持仓必须为 `official_matched`，引用存在的人物，并有金额区间、申报 ID 和官方 HTTPS URL；
- House URL 只接受 `disclosures-clerk.house.gov`，Senate 只接受 `efdsearch.senate.gov`，OGE 只接受 `oge.gov`/`www.oge.gov`；
- 未解析、人工申请中、修订替代或来源失败记录留在审计层，不伪装成规范交易/持仓。

`current.json` 是已部署生产入口。当前处于 `bootstrap_empty`：只发布来源健康与覆盖状态，不包含任何尚未通过人工复核的交易或持仓。

当第一批记录通过来源、身份、时效、修订和人工复核门禁后，用审阅输出替换对应规范数组；普通生产发布不得使用 `--allow-empty-production`。该开关只允许所有人物、交易和持仓均为空的显式启动版本，不能绕过非空数据的校验。
