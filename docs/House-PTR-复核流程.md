# House PTR 复核流程

本流程把官方原件发现、机器抽取和生产事实分开。机器结果不能直接进入 `transactions`。

## 状态流转

1. 年度索引中的 `P` 文件先标记为 `official_raw_unparsed`。
2. 下载器只接受已归档索引中的文档 ID，把 PDF 与首次获取元数据按 SHA-256 归档。
3. 解析器输出 `house-ptr-extraction/v1`，每行包含原件哈希、页码、坐标、原始交易类型和抽取字段，状态为 `awaiting_manual_review`。
4. Clerk 当前议员 XML只生成 BioGuide 身份建议。选区和姓名不能唯一同时匹配时保持 `unresolved`。
5. `create-house-ptr-review` 生成待填写模板。自动任务不得替人填写批准。
6. `promote-house-ptr-review` 只有在全部门禁满足时才输出 `official_matched` 交易片段。

## 复核文件必填项

- `identity.person_id`：格式为 `house:<BioGuide ID>`。
- `identity.evidence_url`：允许的 House、Congress 或 BioGuide 官方 HTTPS 地址。
- `filing.filed_at`：必须保留官方索引的申报日期并包含时区；时间归一政策需由项目确定。
- `source_use_clearance`：状态为 `approved`，并给出可追踪的批准、法律审阅或运营决定编号。
- `review`：总体决定、具名复核者、带时区的复核时间。
- `rows`：每个抽取 ID 都必须 `accepted` 或 `rejected`。拒绝必须说明原因；任何字段更正也必须说明原因。
- `rows[].revision`：普通 `New` 行必须为 `action: none`。amended 或带原交易编号的行必须选择 `replace_prior` 并填写本系统的 `prior_record_id`，或选择 `standalone_correction` 并在该行说明无法可靠关联原记录的原因。

晋级输出将修订决定放在单独的 `revisions` 数组中，并在审计摘要记录数量。官方申报中的 transaction ID 只作为证据，不能直接当作本系统旧记录 ID，也不能单独触发覆盖或删除。

## 生产边界

解析结果、身份建议和 pending 复核文件不能放入 `ingest/current.json`。晋级输出仍需与人物主数据、来源健康状态和数据截止时间合并，再经过生产 builder 的字段、外键、日期、来源域名与状态校验。

真实样本 `20035420` 的 PDF 自带“允许打印、禁止复制”的权限标志，解析输出因此记录 `source_pdf_copy_permission_disabled`，同时强制保留 `source_use_clearance_required`。这只是技术与审计门禁，不是对媒体例外或商业使用资格的法律判断。
