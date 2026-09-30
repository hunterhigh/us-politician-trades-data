# OGE 85 条未入正式候选的固定证据处置

日期：2026-09-30。继续以用户指定的最新 HTML 为前端基准：`C:\Users\admin\Downloads\politician-disclosures (3).html`（SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`）。本轮只读，不修改任何生产事实或工作流。

## 固定证据

承接[OGE 行身份审计](pipeline-oge-row-identity-2026-09-30.md)的 85 条 `(document_id, source_sha256, extraction_id)`。其机器输出 SHA-256 为 `4D5E68BCB829B4A89B93A6E2E16B83C7B9A58EBF4CFA0686B3D7671EC1D95150`。在 review commit `7fb8c1a6949f2444224f5080bce67b51fc8dcadb`、根树 `51f32a4d46769b365f3df27a6d7e1ff8898a29b4` 下，已核对：

- `oge/qualifications/current.json` Git blob `75402e5790288db608dceb8c62202b72fe656e5b`；85 个抽取 ID 各有且仅有一条隔离审计行，四份报告 `promoted_count=0`。
- `candidates/sources/oge-current.json` Git blob `43c1274dd25f3a7092a4e182937482f5eaf284d4`；这 85 个 ID 均不属于各自申报的正式交易。
- `oge/discoveries/0b93cb104ceee9f017016c0fc1ecbfb9c7a4f49962c8c61517b8be33251f5a01.json` Git blob `b4e471c3122c2d92b342910ec946a9729573f6c5`；其官方目录元数据与 OGE 来源 run `36662312152` 的四份记录逐字段相同。该 discovery 是固定 review 内的一份目录快照，不能声称它就是此前资格构建器使用的唯一“当前”目录；资格原因以固定 `qualifications/current.json` 为准。

## 逐文档结论

| 官方文档 ID | 原件 SHA-256 | 缺失行 | 固定资格原因 | 目录依据 |
|---|---|---:|---|---|
| `2c347e1e940d65a885258cfd002c0504` | `afeecc9c3aa44ac6feb73d2a9d81a0c1aa087fc1a7875b6435346499869b2a58` | 69 | `identity_ambiguous` | 目录姓名 Stephen I Miran，机构/职位为 Council of Economic Advisers / Member & Chairman |
| `4b988978d7a864f785258de4002dbf44` | `929019c094c9b9d3a291422cfa087b06871629a6d1b9821313589723c18b98ef` | 7 | `identity_ambiguous` | 同目录姓名，机构/职位为 Federal Reserve System Board of Governors / Governor |
| `86c03b37455893e185258d710034584a` | `890c8d26358200f20a6c8351694d0918030da6a7208b360f8f6bfa19afa7d3d7` | 1 | `identity_ambiguous` | 同上 |
| `d3bcc865ef5e39f585258718002e4aa5` | `916e87c5f7428597348e74f735aac4180e1714cd417df021ef22cdd9041d3931` | 8 | `extraction_id_invalid_or_duplicated` | Wendy R Sherman；另一官方文档 `1a3bd00f7ac33a6385258718002e4aa3` 有相同 PDF SHA 与同一组 8 个抽取 ID，后者在固定正式候选中 |

前三份合计 77 条：资格代码把目录姓名与机构、职位组成身份键，且同一姓名出现两种机构/职位键，所以整份报告的行被隔离。这是现有规则的明确结果，不足以证明两类职位属于或不属于同一稳定身份；不能单凭姓名跨职位合并。

后一份 8 条：两份 Sherman 官方目录记录均未标注 amended、pending 为 false，PDF 字节相同，申报 URL 和 document_id 不同；固定资格按全局重复抽取 ID 只保留排序在前的官方文档，另一份隔离。这里的 `extraction_id_invalid_or_duplicated` 是固定资格审计原文，尚不是官方“superseded”关系认定。四份报告均无 `amendment_relationship_unresolved` 或 `superseded` 原因，不能把缺席解释为已验证修订链。

机器报告：`%TEMP%\whitehouse-oge-85-dispositions-36662639609.json`，SHA-256 `7BC88BB8DFDD35EC439906FC4FFD95819C575FCED35AA40711516F2CF5BF34BE`，逐条保留文档、原件 SHA、shadow candidate ID、抽取 ID、资格原因与重复 peer。可由 `backend/src/unison_snapshot/pipeline_qa/oge_missing_disposition.py` 使用固定输入重算。

**实施判断：** 暂不更改资格规则。77 条需要官方稳定人物身份或可验证的任职关联；8 条需要官方文档重复/修订关系的独立处置，不能因为同字节而自动补入或删除。已查到的固定审计与现有 fail-closed 规则一致，没有达到“逻辑错误且可完全验证修复”的条件。`projection_ready=false`。
