# 18 份白宫 278-T 固定来源影子重放

上一轮[文档审计](whitehouse-wh-url-8150-document-audit-2026-09-30.md)将 8,150 条 `wh-url` 候选分为 1 份年报与 18 份 278-T。本轮只处理后者，不处理其他 OGE 278-T 或年报，也不修改任何正式候选、生产工作流或 Git refs。用户最新 HTML `politician-disclosures (3).html` 已再次核对 SHA-256 为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`；七个事件日早于可验证行情首日的收益值继续显示“—”。

## 固定来源与结果

- 候选：`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` 的 `candidates/disclosure-current.json`，Git blob `36cabab1f0c02a982cd4b04f09ced0d33d270352`。
- 原件：`evidence=5fe5f0adb3181c5d1552dff3f07336464c3043a7` 路径树中的 18 份白宫 278-T PDF。实际从固定[白宫公开目录](https://www.whitehouse.gov/disclosures/)的对应官方 URL 只读取得 18/18 PDF，逐份重算 PDF SHA-256，全部等于 `evidence` 归档路径所载的 SHA。原件字节留在本地忽略的 `.local`，没有提交到 `code`。
- 抽取：固定 `review` 树中 18 份按各来源指定的 v2/v3/v4 最高版本抽取件；其[来源清单](../backend/tests/fixtures/whitehouse_278t_shadow_sources.json)列出每个路径与 Git blob。实际读取或从同一固定提交的公开原始内容取得字节，18/18 重算 Git blob SHA 均匹配。来源 ID、URL、PDF SHA 及抽取件的 `source_row_count` 均由[重放适配器](../backend/src/unison_snapshot/whitehouse_278t_shadow_replay.py)校验。

18 份抽取件共记录 **8,131 个源抽取行**。这些是抽取器的行，不能单凭计数认作独立视觉普查的 PDF 物理行。[逐行影子清单](../backend/tests/fixtures/whitehouse_278t_shadow_rows.jsonl)为每行保留文档 ID、抽取行 ID、页/行位置、源行摘要、处置及隔离原因；[汇总](../backend/tests/fixtures/whitehouse_278t_shadow_summary.json)绑定清单 SHA-256。

| 处置 | 行数 | 含义 |
| --- | ---: | --- |
| `candidate_existing` | 1,391 | 与固定候选 ID、资产、所有人、方向、交易日、金额及源 URL 一致；没有重新晋级 |
| `parsed_absent_from_candidate` | 4 | review 已解析，固定资格判定为同报告交易重复风险并隔离；保持旁路 |
| `review_quarantined` | 6,736 | review 原本隔离；原原因及源行摘要保留，不晋级 |
| 候选独有或候选字段冲突 | 0 | 在这 18 份固定来源的上述字段中未见 |

4 条差异均出自 `wh-url:60d8d791c71b1049b30637fb`（Stephen Miller 278-T）：第 16、21 行是 2025-08-14 卖出 AMAZON COM INC、金额 $100,001–$250,000；第 19、24 行是同日卖出 MICROSOFT CORP、金额 $15,001–$50,000。固定 `review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` 的 `whitehouse/qualifications/candidate-current.json`（Git blob `ee2529182a9aba9eb2fa310ffa448ab12374d216`）逐行记载 23 条 promoted、4 条 quarantined，后四条均标 `possible_whitehouse_same_report_transaction_duplicate`。因此是有记录的同报告重复风险隔离，不是静默丢失，也尚不能当作事实上的重复交易删除。[机器资格审计](../backend/tests/fixtures/whitehouse_278t_miller_qualification_audit.json)将四个抽取 ID、行号、原因与影子处置对应。所有 18 份与 OGE 目录的文档同源或修订关系仍为 `unknown`。

## 原件重新解析的范围

其中 **11 份原生文字 v2 PDF** 已以当前解析器从实取官方 PDF 再跑一次，共 87 条交易；对照固定 review 抽取，交易 ID、资产、所有人、方向、交易日及金额逐条相同，隔离行 ID/原因亦相同。当前解析器额外返回原始单元格等溯源字段，因此未声称整个 JSON 逐字相等。[有界重放记录](../backend/tests/fixtures/whitehouse_278t_v2_parser_replay.json)记录每份 PDF SHA 和差异判断。

其余 **1 份 v3、6 份 v4** 需要 OCR。本机使用 `C:\Program Files\Tesseract-OCR\tesseract.exe`（5.4.0.20240606）实际有界重跑其中三份：

| 文档 ID 后缀 | 页数 | 固定 review → 当前重跑 | 结果 |
| --- | ---: | --- | --- |
| `1a6bbff2a684fedd76ac9f59` | 22 | 507→507 源行；交易 465→475，隔离 42→32 | 两侧交易 ID 共有 362；review 独有 103，重跑独有 113。共有交易的六个关键字段相同，但 OCR 行 ID/资格漂移，不能替换固定抽取 |
| `82a263659dcbd44a6522ecbc` | 8 | 174→174 源行；交易 5→3，隔离 169→171 | 两侧交易 ID 共有 1；review 独有 4，重跑独有 2。固定候选不可据此删改 |
| `62340683e32b0263e8f0eb20` | 8 | review 191 源行；当前未返回结果 | 当前解析器抛 `OgeCatalogError: Trump 2026 278-T v2 row conservation failed`，失败闭合 |

[OCR 有界机器审计](../backend/tests/fixtures/whitehouse_278t_ocr_bounded_audit.json)绑定 PDF SHA、review Git blob、解析版本与交易/隔离行 ID 的双向差异。剩余四份 OCR PDF 尚未重新解析；其中 113 页文档超出内联 OCR 页数上限。七份 OCR PDF 均已完成原件字节 SHA、固定 review 抽取 Git blob、逐行抽取处置及候选字段对账。6,736 条隔离行的视觉准确度和 PDF 全页完整性仍须独立校验。

治理迁移的后续是以文档 SHA、review Git blob、parser 版本和行 ID 承接 1,391 条现有合格事实，并保留其余行的旧资格/隔离状态。OCR 文档逐页普查与重跑漂移定位是独立来源质量修复，不是这批已合格事实无损迁移的全局前置条件。只有取得 OGE 官方文档 ID/原件哈希或可核验修订链，才判断跨渠道同源与重复。此影子清单既不删除 1,391 条已存在候选，也不把其余 6,740 条写入生产。

实际运行：`replay_whitehouse_278t_shadow.py` 对 18 份固定 PDF/抽取/候选输出 8,131 行；11 份 v2 官方 PDF、一份 v3 和两份 v4 的有界解析器重跑结果如上。复算需要本地保存的原件和抽取字节：

```powershell
python backend/scripts/replay_whitehouse_278t_shadow.py --candidate .local/disclosure-candidate.json --document-audit backend/tests/fixtures/whitehouse_wh_url_8150_document_audit.json --source-manifest backend/tests/fixtures/whitehouse_278t_shadow_sources.json --pdf-dir .local/wh-278t-pdfs --review-dir .local/wh-278t-review --summary-out .local/wh-278t-shadow-summary-replay.json --rows-out .local/wh-278t-shadow-rows-replay.jsonl
```
