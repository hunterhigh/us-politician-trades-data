# 首发前的规范输入示例（历史）

本目录的`current.json`和`code`分支根目录的`manifest.json`、`board/`保留早期`bootstrap_empty`启动样本，不是当前正式生产入口。正式快照应从`main`分支的`manifest.json`固定到具体提交读取；当前工作流从`review`候选合并披露与授权行情，经过预验收后先发布`market`再发布`main`。最新实现和数据量见[实现状态与下一步](../docs/实现状态与下一步.md)。

下面记录的是早期手动发布路径对本目录 JSON（例如`ingest/current.json`）的约束；这里不是原始 PDF、抓取日志或密钥存放处。

生产输入必须满足：

- `meta.is_demo` 明确为 `false`，`meta.data_cutoff_at` 为带时区时间；
- `people`、`transactions`、`reported_holdings`、`security_market_data`、`source_health` 均存在；
- 启动样本的行情为空；当前正式生产已有`market`分支授权行情，不能沿用这一旧限制；
- 交易和持仓必须为 `official_matched`，引用存在的人物，并有金额区间、申报 ID 和官方 HTTPS URL；
- House URL 只接受 `disclosures-clerk.house.gov`，Senate 只接受 `efdsearch.senate.gov`，OGE 只接受 `oge.gov`/`www.oge.gov`；
- 未解析、人工申请中、修订替代或来源失败记录留在审计层，不伪装成规范交易/持仓。

`current.json` 是历史启动输入；其中的`bootstrap_empty`只描述当时的样本，不描述当前`main`。

隔离记录不能进入规范财务数组。普通生产发布不得使用 `--allow-empty-production`；该开关只允许所有人物、交易和持仓均为空的显式启动版本，不能绕过非空数据的校验。
