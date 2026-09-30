# House PTR 四份扫描报告的文件级闭合核对

日期：2026-09-30。仅在隔离分支只读核对固定 `evidence` 提交 `5fe5f0adb3181c5d1552dff3f07336464c3043a7` 与固定 `review` 提交 `a814fde2bb9664c2b97981a32ce86ab9b3daf223`；没有修改官方原件、存档资格或正式候选。最新前端 HTML 的 SHA-256 为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。

以下“物理行”指原件图像中可见的独立交易行。OCR 或解析返回成功只说明找到了某些行，不能证明整份文件已完整。扫描表格没有可机器核对的总行数；物理行计数来自逐页图像核对，仅用于这四份固定文件的人工审计，不写入生产解析器特例。

## 9116260：7 行可见，仅抽取 2 行，文件未闭合

原件 SHA-256：`40abd1de969ff344fab5f3c8950a17ebb8ab5df993ded910f00e48d0d47e385d`。PDF 两页。存档 `review` 标为失败；本机旧、新解析器均返回“成功”及两行，二者都因 OCR 置信度不足隔离，合格候选 0。

| 页 | 可见物理行 | 本机处置 |
|---|---|---|
| 1 | Adobe Inc，purchase，C（$50,001–100,000） | 抽取并隔离，OCR 低置信度 |
| 1 | Salesforce Inc，purchase，B（$15,001–50,000） | 抽取并隔离，OCR 低置信度 |
| 1 | The Trade Desk Inc Class A，purchase，B | 未抽取 |
| 1 | Upstart Hldgs Inc，partial sale，B | 未抽取 |
| 2 | HubSpot Inc，purchase，B | 未抽取 |
| 2 | ServiceNow Inc，purchase，B | 未抽取 |
| 2 | Advanced Micro Devices（原件文字截断），partial sale，D（$100,001–250,000） | 未抽取 |

第二页日期栏肉眼可见日期；Tesseract 对其中一行产生如 `)}69/26)` 的畸形词，另一行的 `6/3/26]` 虽可解析，却位于当前固定 58% 页高起点之前，未成为交易行。第一页另有两行日期词可解析，但同一行 OCR 的竖线导致整行日期串无法解析。不能把“解析成功 2 行”视为文件成功；生产重试与晋级保持阻断。

## 9116326：8 行可见，仅抽取 2 行，文件未闭合

原件 SHA-256：`be114957d03486636e639fe9d6c124c71ba8634e2f38f5dfb672eee92502ad2d`。PDF 两页。存档标为失败；本机旧、新解析器均返回两行，均因字段缺失或 OCR 置信度不足隔离，合格候选 0。

| 页 | 可见物理行 | 本机处置 |
|---|---|---|
| 1 | Schwab Short Term U S Treasury ETF，partial sale，C | 抽取并隔离；资产文字畸形、金额缺失 |
| 1 | Salesforce，purchase，B | 未抽取 |
| 1 | ServiceNow，purchase，B | 未抽取 |
| 1 | HubSpot，purchase，B | 抽取并隔离；金额缺失 |
| 2 | The Trade Desk Inc Class A，partial sale，B | 未抽取 |
| 2 | Upstart Hldgs Inc，partial sale，B | 未抽取 |
| 2 | US Treasury NT 1 5 / 08XXX（原件如此标示），sale，E | 未抽取 |
| 2 | US Treasury BILL27 U S T BILL DUE 02/25/27，purchase，E | 未抽取 |

第二页 OCR 中存在日期词，但该扫描变体的交易区位于现行表头几何选择区域之外，导致整页遗漏。没有可靠的通用行数闭合信号；不能以局部成功释放文件。现阶段不加入报告 ID 特例，也不放宽几何规则以制造未经验证的行。

## 9115762：明确无交易，可闭合本地抽取差异

原件 SHA-256：`0f8693b3d1df1b5620ca75a7cfcd8ec1150e3cc22a264afd81f614756e72fc3b`。PDF 一页，页面明确写着 “Nothing to report for March 2026”，无可见交易行。存档为成功、0 行、明确无交易处置。本机原先失败并非发现未识别交易，而是 Windows Tesseract TSV 的一个词含原始 `"+`；`csv.DictReader` 把它视为未闭合引号，将后续数百个物理词行并吞，仅保留 9 个 OCR 词，丢掉无交易声明。

隔离代码改为按 Tesseract 的物理制表符行读取，并检验字段数量。相同固定原件的真实重放恢复 260 个有效 OCR 词，解析为 0 行并提供明确的 `document_disposition`，与存档的无交易区域坐标 `[117.72, 429.48, 792.0, 438.84]` 一致；固定名册资格为 `qualified_no_transactions`。这是可复现的通用 TSV 读取修复，并有含原始引号的回归测试。它不提供其他扫描件的行完整性证明。

## 9115813：存档成功也是不完整证据，文件未闭合

原件 SHA-256：`737955c7c26c497eda37f4378e1af51409b6231204a82d7ae2c3f25c10e0ae84`。PDF 两页，逐页图像可见第一页 4 行、第二页 5 行，共至少 9 条市政债券 purchase 物理行。下表为图像目视证据，资产文字按可辨部分记载，不是可晋级的机器抽取结果。

| 页及行 | 可见资产文字 | 可见交易日期 | 金额勾选格 | 本机处置 |
|---|---|---|---|---|
| 1-1 | Illinois St Toll Hwy AU RV | 2026-04-08 | C | 未抽取 |
| 1-2 | Honolulu HI City & C NTY SR E | 2026-04-08 | C | 未抽取 |
| 1-3 | Metropolitan Atlanta RAP SR A RV | 2026-04-09 | C | 未抽取 |
| 1-4 | San Antonio TX ELEC & GA RV | 2026-04-09 | C | 未抽取 |
| 2-1 | North TX TWY AUTH RV | 2026-04-09 | C | 未抽取 |
| 2-2 | Kentucky ST PPTY & BLDGS RV | 2026-04-09 | B | 未抽取 |
| 2-3 | Energy Northwest WA ELEC RV | 2026-04-09 | B | 未抽取 |
| 2-4 | Anne Arundel CNTY MD | 2026-04-09 | C | 未抽取 |
| 2-5 | Richmond VA SR A | 2026-04-15 | B | 未抽取 |

存档抽取虽标为成功，只含第二页一条资产名畸形的行（`Municipal Bond Px}IP HIRICI EJ} LT]`），OCR 置信度 21.49，方向及金额缺失，隔离，候选 0；其 `2026-04-29` 日期在图像上是通知日期，不能作为交易日期。本机旧、新解析器均未取得可识别交易行而失败。OCR 日期栏有 `ae2026`、`4e2026` 等畸形词，不能按猜测补齐。

因此“存档成功 / 本机失败”不是交易消失的 parser 回归，也不能证明存档完整。仍阻断生产资格重判；需先取得可信逐行文字/日期/方向/金额与页数闭合证据。页面印有跨页编号，但 PDF 页数与印刷编号的关系尚未可靠查清，不据此宣称缺页。

## 发布边界

四份中仅 `9115762` 的零行结论有明确声明与通用 OCR 读取修复双重验证。`9116260`、`9116326` 的本机“成功”是假阳性的文件完整性状态；`9115813` 的存档“成功”也是不完整证据。三份仍须留在文件级阻断队列；本审计没有启动重试、写入正式候选或修改生产工作流。即使隔离分支的既有候选对比为零移除、零改值，也不能代替这三份的逐行闭合。
