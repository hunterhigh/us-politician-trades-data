# Senate 纸面 PTR：9 份、52 页影子行清单

日期：2026-09-30。状态：**只读页面与格带清点；全部疑似填写格带隔离，未生成交易候选。**逐页页图 SHA、表格横线、填写格带的像素纵界、OCR 原文及不能判读的格带见[机器可读清单](senate-paper-52-page-inventory.json)；重放程序为 `backend/scripts/audit_senate_paper_pages.py`。

## 固定输入与实际结果

使用 `evidence` 固定提交 `80f086267677b8fde34314f24024bdc505d97cb0`。程序验证 9 个页面 manifest 的生成哈希、52 张 GIF 与 manifest 的 SHA-256、页序及尺寸。页面只从该 Git 对象读取，不访问 Senate 网站，不写 `evidence`、`review` 或正式候选。前端基准 `C:\Users\admin\Downloads\politician-disclosures (3).html` 的 SHA-256 再次核对为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。

本机实际运行 Tesseract `v5.4.0.20240606` / `eng` 全页 TSV OCR 和 Pillow `12.3.0` 像素清点。9 张是封面；其余 43 张检测到交易表格横线。40 张按全宽横线定位，较浅的 `ec20cd93-…` 报告 3 张改用资产格局部横线定位。两种线定位都只用于找格带，不解释勾选。第一张表格页的两行印刷示例按固定版式位置排除，其他页面不做“空白即零交易”的推断。

| Document ID | 归档页 | 表格页 | 有填写信号的格带 | 至少一格有 OCR | 资产或日期有 OCR | 资产与日期均有 OCR |
|---|---:|---:|---:|---:|---:|---:|
| `068274e1-4b7a-4453-a242-563dde4c10d8` | 5 | 4 | 48 | 20 | 17 | 13 |
| `3a4c5095-028a-4614-a692-836719da4e63` | 5 | 4 | 56 | 51 | 50 | 39 |
| `929216d5-5dbd-429c-858c-1e9332924627` | 9 | 8 | 124 | 119 | 117 | 58 |
| `a0d25e8f-fe54-4328-a7ea-504da008742b` | 7 | 6 | 85 | 78 | 78 | 58 |
| `d02263c3-381d-4ee9-8d84-2c44d9baa59e` | 5 | 4 | 48 | 45 | 41 | 24 |
| `d337c392-e0aa-428e-be93-44a327b90d08` | 6 | 5 | 68 | 46 | 46 | 30 |
| `ec20cd93-6702-4a29-b3a6-983f4b17f365` | 4 | 3 | 37 | 22 | 22 | 10 |
| `f028d2ce-4ab7-41a8-a67a-91675b6941d7` | 6 | 5 | 77 | 73 | 72 | 56 |
| `f873aeb4-adbb-4934-a188-79416a2e4c76` | 5 | 4 | 44 | 33 | 31 | 11 |
| **合计** | **52** | **43** | **587** | **487** | **474** | **299** |

“有填写信号”是表格格带中资产格墨迹抽样达到阈值或某格有 OCR 词；包括账户/小节标题，**不是交易数量，也不是经逐行人工确认的穷尽清单**。其中 100 个格带仅有图像墨迹信号，全文 OCR 连一格词也没可靠返回。即使 OCR 返回了 `X`，当前仍未建立逐格裁剪、方向/金额列界、标记唯一性和置信度验证；587 个格带在清单中一律为 `quarantined_unverified_paper_row`。表中 299 个资产与日期都有 OCR 的格带也未自动晋级。

`ec20cd93-…` 的三个表格页在整页 OCR 中有显著遗漏；肉眼对照固定 GIF 可见仍有清楚填入的资产和勾选。初版墨迹阈值把该浅扫描中的 11 个空白格带误报为填写；在这三页，空白格带采样为 52–98，真实可见填写格带起于 177，因此改用 150 阈值并再次完整重放。其他报告中仅靠墨迹定位的格带最小采样为 177。本阈值是页内盘点信号，不是“没有信号即空行”的证明。前一[单行重放](senate-paper-row2-replay.md)中的方向格 OCR 为低置信宽框 `Ba`、金额格无词，也具体说明不能把日期和资产可读等同交易已解析。

## 复算与下一步

在含固定 evidence Git 对象的仓库根目录，使用相同版本 Tesseract / `eng` 与 Pillow：

```powershell
$env:PYTHONPATH='backend/src'
python backend/scripts/audit_senate_paper_pages.py --repo . --tesseract 'C:\Program Files\Tesseract-OCR\tesseract.exe' --output docs/senate-paper-52-page-inventory.json
python -m unittest discover -s backend/tests -p 'test_senate_paper*.py' -v
```

本机实际全量运行清点三次用于阈值修正，最终 JSON SHA-256 为 `238732d74afcdabd5e8991c3168e4e6ca185fbd02a67121ccc3b3e3c0ba4cd7d`；最终相关测试 8 项通过。没有运行通用纸面交易解析、候选对账或生产工作流。下一步须按固定页图为每个格带区分标题、交易和不可读行，保存逐格裁剪与 mark 证据，并做全页物理行守恒。证据未闭合前不实施通用自动晋级；本轮也没有把 587 格带投影到最新 HTML 的交易数组。
