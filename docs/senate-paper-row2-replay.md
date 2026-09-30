# Senate paper PTR 固定第 2 行保守重放

日期：2026-09-30。状态：**1 个真实页面行位置已有可复算 OCR 观察，隔离；9 份报告的逐行提取和守恒尚未完成。**

## 固定输入与工具

- 代码基线：`code`=`11c82fc8dd65e7ddbb0cb41308b8ebfc00c29e6f`。
- 官方归档证据：`evidence`=`80f086267677b8fde34314f24024bdc505d97cb0`，document ID `068274e1-4b7a-4453-a242-563dde4c10d8`，第 2 页，GIF SHA-256 `327fe3996f395a7ad9b5355ff813bcfe33126e8cfdbbb91616655399e8574c89`。程序从该固定 Git 对象读取页图，校验 SHA-256、GIF 头和 3400×4400 尺寸；不重新采集或写入 `evidence`/`review`。
- OCR：Tesseract `v5.4.0.20240606`、`eng`，调用现有 `tesseract_words` 的原页 TSV 参数 `tesseract <固定GIF> stdout -l eng tsv`。版本不同时重放失败关闭。此版本通过本机 `winget` 安装；原先缺少可运行 OCR 工具的阻塞已解除。
- 最新前端基准 `C:\Users\admin\Downloads\politician-disclosures (3).html` 的 SHA-256 复核为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。本样本不产生前端交易。

## 行观察和处置

原 GIF 肉眼定位第 2 页印刷行号 `2`，像素纵向边界 `[2210,2355)`。行定位目前是这个固定页的人工核对，不是全页自动行数证明。完整可机器比较的 OCR 词、坐标和置信度保存在 `backend/tests/fixtures/senate_paper_row2.json`。

| 项目 | 本次实际观察 |
|---|---|
| 资产列 OCR | `(S) ELCM2 LLC`；三个词的置信度分别为 91.120247、91.030975、96.907616。这里只保存 OCR 原词，不推断证券代码。 |
| 日期列 OCR | `1/9/26`，置信度 96.778008。这里只保存原始字串，不填入候选交易日期。 |
| 交易方向格 OCR | `Ba`，置信度 45.94463，覆盖过宽；无法据此唯一核实 purchase/sale/exchange 勾选。 |
| 金额格 OCR | 未返回词；无法据此核实金额区间勾选。 |
| 处置 | `quarantined`：`paper_transaction_type_mark_unverified`、`paper_amount_mark_unverified`、`paper_report_row_census_incomplete`。交易方向和金额保持 `null`。 |

原图中可以看到表格与笔迹，但现有全文 OCR 输出未为关键勾选格提供足够证据。此样本证明“归档 GIF → 固定 OCR → 行位置 → 原词与隔离理由”的只读链路可以重放；不证明全页共有多少真实交易行、这份报告已解析，亦不构成 candidate。现有 `senate_paper.py` 以日期、方向、金额三类 OCR 锚中至少两类发现行；本行只有日期类可信，因此需要后续页内网格行定位和勾选格图像判读，才能建立全页逐行守恒。不能只把 Tesseract 漏掉的 `X` 当作空白或猜测其所在金额档。

## 复算

在仓库根目录、安装上述 Tesseract 和 `eng` 后：

```powershell
$env:PYTHONPATH='backend/src'
python backend/scripts/replay_senate_paper_sample.py --repo . --tesseract 'C:\Program Files\Tesseract-OCR\tesseract.exe' --fixture backend/tests/fixtures/senate_paper_row2.json
```

本机实际运行该命令：页图与工具版本校验通过，输出与 fixture 逐字段相同。另运行 `python -m unittest discover -s backend/tests -p 'test_senate_paper_sample.py' -v`，3 项通过。没有运行其余 51 页的 OCR、全报告 row census、候选对账或生产工作流。

下一步应在固定 9 份、52 页上依据表格线定位所有物理行，再对每行的方向和金额格保留裁剪坐标、像素/标记判断与置信度；对日期、资产、行归属和勾选有疑义的行持续隔离。只有完整行数和资格条件闭合后才可考虑候选映射。
