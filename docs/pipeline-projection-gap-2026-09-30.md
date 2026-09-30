# vNext ledger 到正式候选投影缺口（只读审计）

审计日期：2026-09-30。最新前端基准为 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`；用户决定交易日早于可验证行情首日的七个收益字段标为不可计算，页面显示“—”。本审计没有改动该 HTML、`review-input/`、生产分支或正式候选。

固定输入：GitHub Actions bundle run `36662639609`，bundle manifest `pipeline-shadow-36662639609-1`，代码 commit `96d86b2cf6e4dac003133d88bad061bf4f2669a1`。本地只读输入位于 `%TEMP%\whitehouse-bundle-36662639609`。审计器验证 manifest 声明的 4 个输出哈希、13,572 行守恒、每行 schema 与已解析文档绑定。结果为 5,060 条 qualified（House 8、Senate eFD 12、OGE 5,040），全局唯一 `candidate_id` 仅 5,052 个。此固定三源 bundle 只是抽样 shadow 运行，不是全量正式候选。

## 字段级结果

正式交易 schema 见 `backend/src/unison_snapshot/builder.py` 的 `FIELDS["transactions"]`；身份和报告上下文见 `house_candidate.py`、`senate_candidate.py`、`oge_candidate.py`。ledger 的 `source` 嵌套对象只给 `source_id`、`document_id`、`source_url`、`source_sha256`，不能等同于正式交易对象或人物对象。

| 字段/关系 | House qualified | Senate qualified | OGE qualified | 投影要求 |
|---|---:|---:|---:|---|
| asset_name、transaction_date、transaction_type、amount_low、amount_high 的选定规范值 | 8/8 | 12/12 | 5,040/5,040 | 可作为待核对观察值；仍须旧/新逐行对账 |
| owner 的选定规范值 | 0/8 | 12/12 | 5,040/5,040 | House 必须从同一固定证据的旧资格产物或新增可验证观察补足，不能默认 `Self` |
| ticker 的选定规范值 | 0/8 | 10/12 | 0/5,040 | 只能从原件明示值或已有受控映射规则取得；没有则允许 `null` |
| person_id 与人物对象 | 0 | 0 | 0 | 固定官方身份目录、无歧义人物绑定和人物外键校验 |
| filing_id、filed_at | 0 | 0 | 0 | 固定报告/目录元数据，确认申报时间及前端截止规则 |
| 正式交易 id | 0 | 0 | 0 | 不得直接使用全局不唯一的 `candidate_id`；要和既有正式 ID 规则对齐 |
| instrument_type、期权条款、ticker_mapping_basis、position_effect | 0 | 0 | 0 | 按来源现有保守资格规则补足或保持允许的 `null`/`unknown`，不能猜测期权条款 |
| revision/duplicate 决议 | 0 | 0 | 0 | Senate amended、OGE 同字节不同文档和 House 修订均须独立决议 |
| verification_status = official_matched | 0 | 0 | 0 | 只能在以上来源、身份、修订、证据和正式构建验证闭合后赋值 |

“0”表示 ledger 顶层及选定 observations 不提供此正式字段，不表示旧生产数据不存在。House 行有 `source_evidence`，但仍没有完整正式事实绑定。bundle 文档 manifest 也没有人物、申报时间或修订决议字段。

## 全局 ID 冲突的 8 对

以下每对均来自两个不同 OGE 官方 `document_id`，其 PDF SHA-256 相同：`916e87c5f7428597348e74f735aac4180e1714cd417df021ef22cdd9041d3931`。文档 ID 为 `1a3bd00f7ac33a6385258718002e4aa3` 和 `d3bcc865ef5e39f585258718002e4aa5`；共同的 `candidate_id` 逐项为：

1. `916e87c5f7428597:1`
2. `916e87c5f7428597:2`
3. `916e87c5f7428597:3`
4. `916e87c5f7428597:4`
5. `916e87c5f7428597:5`
6. `916e87c5f7428597:6`
7. `916e87c5f7428597:7`
8. `916e87c5f7428597:8`

审计器用 `(source_id, document_id, source_sha256, candidate_id)` 验证行作用域唯一。该复合键只是识别 ledger 行，不代表两份披露都应正式发布，也不是最终交易 ID。文档重复与修订必须另外判定，不能根据字节相同直接删其中一份。

## 最小安全接入路径

1. 固定同一次来源运行的 `evidence`、`state`、`review` commits 与 bundle 哈希；读取对应来源的正式候选/资格产物，建立以官方文档 ID、原件 SHA 和行证据定位为键的映射。每一条映射必须一对一，差异或缺失进隔离，不做姓名推断或默认值补齐。
2. 复用来源现有身份、申报时间、instrument/期权和修订规则，产出独立的映射审计表。OGE 上述 8 对先完成官方报告关系判定。对相同 PDF 内容的两个文档保留两个 scoped 观察身份，再按独立决议决定候选事实命运。
3. 将投影候选与旧候选按人物、申报、正式交易 ID、交易字段逐项比较；报告新增、删除、改变和重复，验证每份文件的行数守恒。把差异审阅结果送入最新 HTML 的看板、人物、股票和缺失收益语义验收。
4. 仅在上述差异、身份、修订、证据、前端测试和回退演练闭合后，接入现有完整发布门禁；在此之前 vNext bundle 只读、`projection_ready=false`。

只读门禁模块：`backend/src/unison_snapshot/pipeline_qa/projection_audit.py`。它不会生成正式交易或写生产 ref。测试：`backend/tests/test_pipeline_projection_audit.py`。
