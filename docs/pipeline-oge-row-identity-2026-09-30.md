# 固定 OGE shadow 行身份回连审计

日期：2026-09-30。最新前端基准是用户指定的 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。本审计只读；不生成正式候选、不裁决修订或重复、不更改生产工作流。

## 固定输入

- 三源 bundle：run `36662639609`，manifest run ID `pipeline-shadow-36662639609-1`，`candidate_rows.json` SHA-256 `C5B893DBFC00F9EBAD1509F6F32C7AC9E1B43A563FF20655A2EA7EB0210EC348`。
- OGE 固定 review commit `7fb8c1a6949f2444224f5080bce67b51fc8dcadb`，通过 GitHub commit 对象确认其根树 SHA-1 `51f32a4d46769b365f3df27a6d7e1ff8898a29b4`；递归树完整，`truncated=false`。固定 OGE 来源候选 Git blob SHA-1 `43c1274dd25f3a7092a4e182937482f5eaf284d4`。
- 对 5,040 条 OGE qualified 行涉及的 **309 份**抽取文件，按 bundle manifest 指定 parser_version 选择固定树中的路径：308 份 `oge-278t-pdf-v2.json`，Trump 2026-09 报告 `e590116fc9631e9885258e7a002de209` 一份 `oge-278t-pdf-v6.json`。下载总计 2,447,169 字节，逐文件核对固定树的 Git blob SHA-1、长度、官方文档 ID、来源 URL、原件 SHA-256 和解析器版本。最初误取 Trump 的 v2 只会找到其 228 条旧交易，漏掉 155 条；按 manifest 改用 v6 后这 155 条全部按行定位闭合。
- Trump 报告另核对了固定 evidence commit `5fe5f0adb3181c5d1552dff3f07336464c3043a7` 中 PDF 的 SHA-256 `833c3b4810eaf2e83a3b27867af149634e65145dc4577c753ef20f6b6d515dcf` 与 Git blob SHA-1 `981357f1927efe81fd9fabcaf2e79499206cabf8`。此前对普通 v2 缓存进行 v6 等价检查失败，原因是缓存确为较旧解析版本；本审计采用固定 review 提交中实际存在、与 bundle manifest 相同版本的 v6 抽取，不将不同解析版本视作等价。

## 结果

| 检查 | 实测 |
|---|---:|
| OGE qualified shadow 行 | 5,040 |
| 用官方 `document_id` + 原件 SHA + 页码 + 选定打印行号唯一绑定固定抽取 `extraction_id` | 5,040 |
| 绑定后，六项规范字段差异（资产、日期、方向、owner、金额上下限） | 0 |
| 固定正式来源候选以 `extraction_id` + `filing_id` + 来源 URL 命中 | 4,955 |
| 旧的纯值对照中 854 条重复值歧义，现已用抽取行 ID 消除 | 854 |
| 已绑定抽取 ID、但固定正式来源候选缺失 | 85 |
| PDF 同字节不同官方文档造成的全局 `candidate_id` 冲突 | 8 对，完整保留 |

85 条缺失集中于四份文档：`2c347e1e940d65a885258cfd002c0504`（69）、`d3bcc865ef5e39f585258718002e4aa5`（8）、`4b988978d7a864f785258de4002dbf44`（7）、`86c03b37455893e18525860f0027ddf7`（1）。这些行已有准确抽取 ID，不能按同值迁移到其他申报。其缺席是当前正式资格/修订/重复规则与 shadow 行级资格之间的差异，原因和最终处置尚未裁决。尤其 `d3bcc...` 与 `1a3bd...` 共享 PDF 字节，不可用字节相同自动删除或自动补入。

只读结果保存于 `%TEMP%\whitehouse-oge-row-identity-audit-36662639609.json`，SHA-256 `4D5E68BCB829B4A89B93A6E2E16B83C7B9A58EBF4CFA0686B3D7671EC1D95150`，包含 85 条缺失行的 scoped ID 与抽取 ID，以及 8 对冲突。该临时文件不是生产资产；代码可按固定输入重算。

## 复跑及边界

模块 `backend/src/unison_snapshot/pipeline_qa/oge_row_identity.py` 的 `audit_oge_row_identity` 需要本地固定 bundle、309 份抽取文件、固定 review 根树 JSON、根树 SHA、review commit、固定正式候选文件及其 blob SHA。调用前应由 GitHub commit API 验证 `review_commit -> tree SHA`，再读取该树；模块核对每个文件属于该树。抽取文件本地按 `<document_id>.json` 放置，其 Git 路径由 bundle manifest 的官方文档 ID、原件 SHA 和 parser_version 唯一生成。不能用“当前最新”分支文件替代。

行身份依赖解析器已保留的打印行号与页码，而不是仅靠同值匹配。旧 v2 抽取中多数交易没有 `cells` 或物理 `source_rows`，因此本轮未再证明 5,040 个 `physical_index` 对 PDF 每一格的字节级映射；bundle 原有适配器在其运行时以来源原件重建 source_rows 并完成守恒，本审计通过固定抽取元数据、页码、打印行号和规范值独立交叉核对。若未来出现同页同打印行号的两个抽取行，模块会失败关闭。

正式投影仍需处理 85 条资格差异、同字节不同申报、amended 关系、人物与 filing 上下文，并完成全量差异、最新 HTML 验收及回退演练。结果始终 `projection_ready=false`。
