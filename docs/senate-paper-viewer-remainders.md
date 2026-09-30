# Senate 纸面 PTR：5 份 viewer 的物理格位守恒

日期：2026-09-30。固定 evidence 提交
`80f086267677b8fde34314f24024bdc505d97cb0`。本文在此前
[首份 65 格审计](senate-paper-first-report-row-audit.md)基础上，补齐另 4 份
只有 viewer、没有正式抽取的纸面 PTR。新生成器
`backend/scripts/audit_senate_paper_viewer_remainders.py` 逐页校验官方 GIF
和页面 manifest 哈希，并把每个表格格位的页号、原图纵界、整行、资产格和
日期格裁剪 SHA-256、资产墨迹信号、旧 OCR 原文保存到
[机器可读格位账本](senate-paper-viewer-remainders.json)。所有格位的
`candidate_transaction_id` 均为 `null`。

| 纸面报告 ID | 官方页/表格页 | 物理数据格 | 标题文字候选 | 外观空白待核 | 内容或噪声未决 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `3a4c5095-028a-4614-a692-836719da4e63` | 5/4 | 65 | 5 | 9 | 51 |
| `a0d25e8f-fe54-4328-a7ea-504da008742b` | 7/6 | 99 | 6 | 14 | 79 |
| `d02263c3-381d-4ee9-8d84-2c44d9baa59e` | 5/4 | 65 | 6 | 17 | 42 |
| `ec20cd93-6702-4a29-b3a6-983f4b17f365` | 4/3 | 48 | 2 | 11 | 35 |
| **本轮 4 份** | **21/17** | **277** | **19** | **51** | **207** |

每份第一页是封面；首张表格页的两行印刷示例不计入数据格。每页的
`grid_line_centers` 相邻两线界定一个格位，因此各报告均满足
`标题文字候选 + 外观空白待核 + 内容或噪声未决 = 物理数据格`。
“标题文字候选”只表示 OCR 的资产格以冒号结尾且日期格无文字；
“外观空白待核”只表示旧清单没有填写信号且资产墨迹抽样低于 150。
这两类仍是隔离观察，既不证明交易行不存在，也不凭图像信号确定
方向、金额、资产、日期或身份。特别是浅扫描 `ec20cd93-…` 的 OCR
遗漏明显，不能把没有 OCR 词的格位直接算作空交易。

连同此前 `068274e1-4b7a-4453-a242-563dde4c10d8` 的 5 页、65 格，
5 份仅有 viewer 的纸面报告共 **26 页、21 张表格页、342 个物理数据格**。
首份报告的 65 格另有逐格勾选审计：36 个交易样行仍隔离、9 个小节标题、
20 个空白或 OCR 噪声格。本文没有把这 36 行升级为候选，也没有将
新清点的 277 格解释成交易数。

固定 131 份目录中另外 4 份纸面报告已有旧抽取，共 9 行；旧正式候选
只有 `f873aeb4-adbb-4934-a188-79416a2e4c76` 的 1 行。
[131 份覆盖审计](senate-efd-shadow-coverage-2026-09.json)逐份列出
这些旧状态。该 1 行来自另一份已抽取纸面报告，与本次 5 份 viewer
的 342 格无交集；本轮没有改写、撤销或新增正式候选。

为闭合全部 52 页的物理格位，同一生成器还只读清点了这 4 份旧抽取报告。
[旧抽取报告格位账本](senate-paper-legacy-extracted-grid.json)记录 26 页、
22 张表格页、362 个物理数据格：17 个标题文字候选、49 个外观空白待核、
296 个内容或噪声未决。逐报告格位分别为 134、82、82、64；旧抽取的
9 行不能替代这些物理格位，也没有在本账本中重新推断旧候选的页内
定位。合并首份 65 格、其余 viewer 277 格和旧抽取报告 362 格，
**9 份/52 页/43 张表格页共 704 个物理数据格**，每格均有唯一
`(document_id, page_number, grid_slot)` 定位和原图裁剪哈希。
这一物理格位总数不是交易总数，1 条旧正式纸面候选也没有因本审计改变。

本机重放命令：

```powershell
$env:PYTHONPATH='backend/src'
python backend/scripts/audit_senate_paper_viewer_remainders.py --repo . --inventory docs/senate-paper-52-page-inventory.json --output docs/senate-paper-viewer-remainders.json
python backend/scripts/audit_senate_paper_viewer_remainders.py --repo . --inventory docs/senate-paper-52-page-inventory.json --legacy-extracted --output docs/senate-paper-legacy-extracted-grid.json
python -m unittest discover -s backend/tests -p 'test_senate_paper*.py' -v
```

生成的 JSON SHA-256 为
`c565ddc43af7685059649760e99ac8202d2a56943c52991f8a5512b683f9e043`。
旧抽取报告格位 JSON SHA-256 为
`c6676354f49c5904ac3f139bed16e4490181d701191d02a1bbb0bf6f7509e22e`。
本机实际运行上述两次生成器以及相关 12 项测试，均通过。最新前端基准仍为
`C:\Users\admin\Downloads\politician-disclosures (3).html`，其固定
SHA-256 为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。
