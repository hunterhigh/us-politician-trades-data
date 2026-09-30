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
| Part 7 交易 | 10 | 0 | 0 | 10 | 表头未被常规表格识别，虽从逐行文本严格恢复 10 行，但印刷编号核对未完成；申报日期未核实；与 278-T 尚未去重；生产 opt-in 未开启 |

两部分共 30 个解析器已检测行，在清单中各有页码、印刷行号（如可读）、稳定行 ID、处置和原因。Part 6 有 8 条子行的父账户行未闭合；`4.1`–`4.5` 等不会因自身金额可读就绕过父账户关系。独立附注解析和总项/子项重复汇总核验仍为 `not_verified`。Part 7 原始日期和法定金额区间与规范值分别保留，OGE receipt stamp 没有被改成申报日。

报告状态为 `not_verified_complete`，原因是缺少独立于解析器的官方表格行普查；30 行守恒只证明检测行均有去向。修订/重复报告关系缺少可核验链，状态为 `not_verified`。该年报仍是影子结果，没有进入生产候选。OGE 年报工作流已有 Part 7 解析恢复路径，但本固定输入的 `part7_numbering.row_reconciliation_complete=false`，生产审计仍为 `promoted_to_candidate=0`；影子适配器即使收到额外 `part7_opt_in_verified=true` 字段也不会开启晋级。

后续若要处理正式候选，需要独立行普查、Part 6 所有人及父子/附注证据、Part 7 印刷编号与申报日、跨 278-T 去重、修订链和前端部分报告语义的逐项闭合。此文档不把这些条件当作已实现。

本分支实际运行：`python -m unittest discover -s backend/tests -p 'test_oge_annual*.py' -v`（19 项通过）；固定 PDF 的哈希核验和解析器精确重放也已实际执行。
