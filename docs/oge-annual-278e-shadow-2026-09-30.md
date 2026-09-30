# OGE 278e 年报 Part 6/Part 7 影子逐行处置

本次只增加独立 OGE 年报影子适配器及固定样本；不改变 `oge-annual-review.yml`、正式候选或生产分支。最新前端验收基准仍是用户指定的 `politician-disclosures (3).html`（SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`）。这里的“qualified”仅是影子账本中单行字段可读的状态，不能当成报告完整或已发布。

## 固定真实输入

- 来源：OGE 官方 Vance 2026 申报的 2025 年度 278e，`source_document_id=40ce0f66f853096985258e27002ddfbb`，原件 SHA-256 `bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d`。
- 原件位于 `evidence` 提交 `5fe5f0adb3181c5d1552dff3f07336464c3043a7` 的 `oge/annual/reports/40ce0f66f853096985258e27002ddfbb/<原件SHA>.pdf`。只读下载后计算原件哈希相同，17 页；PDF 没有加入 `code`。
- 抽取件来自 `review` 提交 `a814fde2bb9664c2b97981a32ce86ab9b3daf223`，路径及 Git blob `e7a23303be33d0ef00a22b71b5ec7d89676930c1` 记录在 [固定处置清单](../backend/tests/fixtures/oge_annual_vance_2026_dispositions.json)。完整抽取件作为 [固定输入](../backend/tests/fixtures/oge_annual_vance_2026.json) 纳入测试，SHA-256 `52c843f25dd21af193f4fdcdf4faa6c7907dff0780531550e34bf983cc1abbf7`。本地以固定 PDF 再运行现有 `extract_annual_pdf`，排除 review 后补的 `source_document_id`、`evidence_archive_path` 两项后，逐字段与固定抽取件完全相等。

## 逐行结果

| Part | 来源结构行 | 来源隔离行 | 影子 qualified | 影子隔离 | 主要原因 |
| --- | ---: | ---: | ---: | ---: | --- |
| Part 6 资产 | 12 | 8 | 0 | 20 | 12 行所有人不明；8 条子行的父账户行仍隔离；另有缺值/行号不可读 |
| Part 7 交易 | 10 | 0 | 0 | 10 | 常规解析器未闭合印刷编号核对；申报日期未核实；与 278-T 尚未去重；生产 opt-in 未开启。下文独立页级核对确认了该页编号 1–10 |

两部分共 30 个解析器已检测行，在清单中各有页码、印刷行号（如可读）、稳定行 ID、处置和原因。Part 6 有 8 条子行的父账户行未闭合；`4.1`–`4.5` 等不会因自身金额可读就绕过父账户关系。独立附注解析和总项/子项重复汇总核验仍为 `not_verified`。Part 7 原始日期和法定金额区间与规范值分别保留，OGE receipt stamp 没有被改成申报日。

报告状态为 `not_verified_complete`，原因是缺少独立于解析器的官方表格行普查；30 行守恒只证明检测行均有去向。修订/重复报告关系缺少可核验链，状态为 `not_verified`。该年报仍是影子结果，没有进入生产候选。OGE 年报工作流已有 Part 7 解析恢复路径，但本固定输入的 `part7_numbering.row_reconciliation_complete=false`，生产审计仍为 `promoted_to_candidate=0`；影子适配器即使收到额外 `part7_opt_in_verified=true` 字段也不会开启晋级。

后续若要处理正式候选，需要独立行普查、Part 6 所有人及父子/附注证据、Part 7 印刷编号与申报日、跨 278-T 去重、修订链和前端部分报告语义的逐项闭合。此文档不把这些条件当作已实现。

## 原件逐页复核与 278-T 去重边界

对固定 17 页 PDF 的全部页面标题做了只读检查：Part 6 只在第 10 页，Part 7 只在第 11 页。第 10、11 页分别渲染并目视核对，同时用独立的整页文本行复核；两页文本 SHA-256 和每个已填写行的印刷编号、资产名、抽取去向记录在[页级核对表](../backend/tests/fixtures/oge_annual_vance_2026_page_census.json)。这份表是源绑定的人工/文本交叉审计，不能自动泛化为其他年报的行普查。

第 10 页 Part 6 有主项 1–13 共 13 行、子项 1.1、2.1、3.1、4.1–4.5、6.1 共 9 行，合计 **22 个已填写物理行**。现有抽取件的 12 个 holdings 和 8 个 quarantine 只覆盖其中 20 行。第 11 行 `Marcus by Goldman Sachs Savings Account`（印刷价值 `$100,001–$250,000`）及第 13 行 `Navy Federal Credit Union Cash Account`（`$1,001–$15,000`）在抽取和隔离两组中均不存在。第 1.1 行虽被隔离，抽取文本把 `2034/2035` 读成 `203412035`；第 9 行印刷价值可见但抽取为缺值。两条漏行和这些字段差异均不直接晋级；当前 20 行账本守恒并非原件 22 行守恒。

第 11 页 Part 7 的印刷 1–10 行均填写，11–20 行是空白模板。**已填写物理行 10/10 被逐行恢复**，印刷编号从页面可独立核对；现有 parser 仍将 `part7_numbering.row_reconciliation_complete` 标为 `false`，因为常规表头识别失败。下表是原件第 11 页与固定抽取件相同的字段，不表示可晋级：

| 行 | 资产 | 方向 | 交易日 | 金额区间 |
| --- | --- | --- | --- | --- |
| 1 | Vanguard Ohio Target Enrollment 2034/2035 Portfolio | 买 | 2025-03-20 | $1,001–$15,000 |
| 2 | Vanguard Ohio Target Enrollment 2040 Portfolio | 买 | 2025-03-20 | $1,001–$15,000 |
| 3 | Vanguard Ohio Target Enrollment 2038 Portfolio | 买 | 2025-04-29 | $15,001–$50,000 |
| 4 | QQQ – Invesco QQQ Trust, Series 1 | 买 | 2025-06-27 | $250,001–$500,000 |
| 5 | DIA – SPDR Dow Jones Indus. Avg. ETF | 买 | 2025-06-27 | $500,001–$1,000,000 |
| 6 | SPY – SPDR S&P 500 ETF | 买 | 2025-06-27 | $500,001–$1,000,000 |
| 7 | DIA – SPDR Dow Jones Indus. Avg. ETF | 买 | 2025-07-14 | $1,001–$15,000 |
| 8 | DIA – SPDR Dow Jones Indus. Avg. ETF | 买 | 2025-10-14 | $1,001–$15,000 |
| 9 | DIA – SPDR Dow Jones Indus. Avg. ETF | 买 | 2025-12-15 | $1,001–$15,000 |
| 10 | Rise of the Rest Seed Fund, LP | 卖 | 2025-01-16 | $100,001–$250,000 |

这些资产不能整体当成上市股票：529 教育组合和私募基金不提供可从名称安全推出的股票代码。Part 7 的 owner 仍为 `Unknown`；OGE `Received` 日期不能代替申报日。交易方向/日期/金额虽有原件印刷证据，仍缺真实申报日、跨 278-T 修订/重复关系及生产 opt-in。

固定正式 `main`=`05391900bd35e5a828cd374c790991b2dc7a12c7` 的 manifest 截止 `2026-09-27T23:59:59Z`；Vance 人物 `oge:a16b930d2cac6095` 分片 `people/ad/783398fdd403fbf1d09ebbb6a916b837462d8c291e016ac3a56a01127d1b322e.json` 含 **0 笔交易**、13 条报告期同为 `2025-12-31` 的**部分持仓**。这 13 条来自白宫 URL `Vice-President-JD-Vance-2025-Annual-Report.pdf`，其归档 PDF SHA-256=`d43f25659a26474faae4df8218ff352e3c01a2eaf17b6ae35ae07649bbe90c3d`，与本次 OGE 目录 PDF 的 `bcad0b4e…` 不同；人物分片已有 Marcus 第 11 行的同名同价值区间持仓，也有若干第 6 部分其他资产。两个渠道内容/修订关系尚未逐页闭合，不能把 OGE 抽取出的资产简单追加到这 13 条上，第 13 行也不能因视觉可读就跳过资格门。

固定 `review`=`edd5f8081c3c068dbfc1350179cd00a21d5441a7` 的统一候选对同一人物为 **0 笔交易**。故第 11 页 10 行与这两个固定候选的 Vance 278-T 交易集合不存在已知重叠，但候选为空不能证明所有官方 278-T 已穷尽或未来不会补入。该 `review` 的白宫公开目录只有 Vance 的 2025 与 2026 两份 278e 链接，未列 Vance 278-T；OGE 来源目录的完整覆盖和跨渠道同一交易关系仍未独立闭合。年报 Part 7 因而继续隔离，不作“无重复”认定。

本分支实际运行：`python -m unittest discover -s backend/tests -p 'test_oge_annual*.py' -v`（20 项通过）；固定 PDF 的哈希核验、解析器精确重放、两页渲染及目视核对均已执行。页级核对测试只校验固定行清单与抽取件的对应，不声称 CI 对未入库 PDF 执行了视觉判断。
