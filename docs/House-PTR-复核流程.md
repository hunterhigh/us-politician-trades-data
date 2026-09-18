# House PTR 自动资格与异常调查

> 当前状态：旧异常调查工具。根据[当前目标与优先级](当前目标与优先级.md)，逐条人工复核、具名复核者和人工批准不再是标准记录的生产门禁。本文保留的命令仅用于调查隔离记录和回归旧实现。

本流程把官方原件发现、机器抽取、自动资格、候选事实和正式发布分开。机器抽取必须先通过资格规则，不能直接进入正式 `transactions`。

## 状态流转

1. 年度索引中的 `P` 文件先标记为 `official_raw_unparsed`。
2. 下载器只接受已归档索引中的文档 ID，把 PDF 与首次获取元数据按 SHA-256 归档。
3. 解析器输出 `house-ptr-extraction/v1`，每行包含原件哈希、页码、坐标、原始交易类型和抽取字段，状态为 `awaiting_automatic_qualification`。
4. Clerk 当前议员 XML以选区和姓名共同匹配 BioGuide 稳定ID；唯一精确匹配为 `matched_automatically`，否则保持 `unresolved`。
5. `qualify-house-ptr` 对每一行执行确定性规则，合格行输出 `official_matched` 候选交易，不确定行输出带原因的 `quarantined` 记录。
6. OCR仅在PDF没有文本层时按需运行，并记录引擎版本与行置信度。旧式纸面表的方向和金额由勾选格表达，需独立表格解析器，不能把OCR文字直接当成完整交易。

## 旧异常调查工具

- `identity.person_id`：格式为 `house:<BioGuide ID>`。
- `identity.evidence_url`：允许的 House、Congress 或 BioGuide 官方 HTTPS 地址。
- `filing.filed_at`：必须保留官方索引的申报日期并包含时区；时间归一政策需由项目确定。
- `source_use_clearance`：状态为 `approved`，并给出可追踪的批准、法律审阅或运营决定编号。
- `review`：总体决定、具名复核者、带时区的复核时间。
- `rows`：每个抽取 ID 都必须 `accepted` 或 `rejected`。拒绝必须说明原因；任何字段更正也必须说明原因。
- `rows[].revision`：普通 `New` 行必须为 `action: none`。amended 或带原交易编号的行必须选择 `replace_prior` 并填写本系统的 `prior_record_id`，或选择 `standalone_correction` 并在该行说明无法可靠关联原记录的原因。

晋级输出将修订决定放在单独的 `revisions` 数组中，并在审计摘要记录数量。官方申报中的 transaction ID 只作为证据，不能直接当作本系统旧记录 ID，也不能单独触发覆盖或删除。

## 生产边界

未资格化的解析结果、身份冲突和隔离记录不能放入 `ingest/current.json`。资格输出仍需与人物主数据、来源健康状态和数据截止时间合并，再经过生产 builder 的字段、外键、日期、来源域名与状态校验，并由原前端完成候选验收。

真实样本 `20035420` 的 PDF 自带“允许打印、禁止复制”的权限标志，解析输出因此记录 `source_pdf_copy_permission_disabled`，同时强制保留 `source_use_clearance_required`。这只是技术与审计门禁，不是对媒体例外或商业使用资格的法律判断。
