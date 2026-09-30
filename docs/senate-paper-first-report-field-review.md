# Senate 首份纸面 PTR：36 行四字段复核

固定报告 `068274e1-4b7a-4453-a242-563dde4c10d8`。只读官方 evidence
提交 `80f086267677b8fde34314f24024bdc505d97cb0`，延续此前
[65 个物理格位审计](senate-paper-first-report-row-audit.md)中已隔离的
36 个交易样行。逐行字段、原图页号与坐标、资产/日期裁剪哈希、OCR 原文和
置信度、方向/金额每个勾选格的像素证据及视觉对照结果，见
[机器账本](senate-paper-first-report-fields.json)。

| 固定页 | 本次复核的交易样行 |
| ---: | ---: |
| 2 | 8 |
| 3 | 12 |
| 4 | 11 |
| 5 | 5 |
| **合计** | **36** |

逐行裁剪使用 RapidOCR `1.4.4` 重读资产和日期。36 行的两个裁剪均
各返回唯一 OCR 文本，资产最低置信度 0.951599、日期最低 0.951288。
原先整页 Tesseract 在第 3、4 页大量漏字；格内重读恢复了这些文字。
随后按固定官方 GIF 生成的原尺寸行图逐行视觉对照：36 行的资产原文、
日期、唯一方向勾选和唯一金额档位勾选均与图像一致。
[视觉对照清单](../backend/tests/fixtures/senate_paper_first_report_visual_review.json)
逐行固定这四个读数，SHA-256
`c6c6e72dce711f5d67c115a3e198d90930d892669049ba796f96005f23bd9c9a`（换行规范化后的 Git 内容）。
机器账本会在重放时验证视觉清单和旧行账本哈希；两者不一致即标出
字段冲突，不能悄悄改写观察值。

四字段读数层面，本 36 行没有剩余歧义：方向 20 行 sale、16 行
purchase；金额档位 18 行 `$1,001–$15,000`、10 行 `$15,001–$50,000`、
3 行 `$50,001–$100,000`、2 行 `$100,001–$250,000`、3 行
`$250,001–$500,000`。金额只表示原表格勾选区间，不表示精确成交额。
资产格共有 6 种逐字原文，日期落在 2026-01-08 至 2026-01-28。

**36 行仍全部隔离。**此结果只闭合了四个图像字段，没有完成纸面报告
的身份、所有人、修订关系、来源资格和正式候选规则审计，也没有把
36 行与其他纸面报告或电子 PTR 去重。机器账本中的每行
`projection_status` 为 `quarantined_no_candidate`，
`candidate_transaction_id` 为 `null`。旧正式纸面候选的 1 行来自另一份
报告；本次未修改它、`review-input/` 或任何生产 ref。

本机重放：

```powershell
$env:PYTHONPATH='backend/src'
python backend/scripts/audit_senate_paper_first_report_fields.py --repo . --rows docs/senate-paper-first-report-rows.json --visual-review backend/tests/fixtures/senate_paper_first_report_visual_review.json --output docs/senate-paper-first-report-fields.json
python -m unittest discover -s backend/tests -p 'test_senate_paper*.py' -v
```

重放输出 JSON SHA-256 为
`d9c964052f9d715decc58b414d1799f6b03e4c8eaeb50359215c9a162d9dc176`。
本机实际运行上述生成器和相关 15 项测试，均通过。最新前端 HTML
基准 `C:\Users\admin\Downloads\politician-disclosures (3).html` 的
SHA-256 仍为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`；
本轮没有写入前端数据。
