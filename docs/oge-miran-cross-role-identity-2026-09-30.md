# OGE Miran 跨任职身份的固定证据边界

日期：2026-09-30。此项只读分析针对[固定三源账本中 85 条 OGE 缺失](pipeline-fixed-binding-map-2026-09-30.md)的 77 条 `identity_ambiguous`，不修改正式候选或人物 ID。

固定 OGE 目录与抽取证据显示，这 77 行只来自三个官方文档，目录申报人均为 `Miran, Stephen I`：

| 官方文档 ID | OGE 目录机构 / 职位 | 固定抽取中的 `filed_at` | 合格影子行 |
| --- | --- | --- | ---: |
| `2c347e1e940d65a885258cfd002c0504` | Council of Economic Advisers / Member & Chairman | 2025-07-21 | 69 |
| `86c03b37455893e185258d710034584a` | Federal Reserve System Board of Governors / Governor | 2025-12-03 | 1 |
| `4b988978d7a864f785258de4002dbf44` | Federal Reserve System Board of Governors / Governor | 2026-04-03 | 7 |

现有 `oge_candidate.py` 的人物键由规范化姓名、机构和职位组成。同一 OGE 目录姓名因此产生两个潜在 ID：CEA `oge:5c6137ac91de2275`、联储 `oge:9e128b9ff17bc0cc`。全目录检测到同名多机构/职位后，规则保守地将三个文档的 77 行全部隔离；固定正式 OGE 来源候选中没有 Miran 人物记录。这个机制保护了不确定身份，但也解释了新旧候选的具体缺口。

跨任职同一人物并非仅凭同名推定：[联储 2025-09-16 官方就职公告](https://www.federalreserve.gov/newsevents/pressreleases/other20250916a.htm)明确称 Stephen I. Miran 宣誓成为理事；[白宫 2026 经济报告](https://www.whitehouse.gov/wp-content/uploads/2026/04/2026-Economic-Report-of-the-President-1.pdf)记载 Stephen Miran 曾任 CEA 主席，2025 年 9 月离开 CEA 转任联储理事。上述两份机构原始材料支持将这两个任职键映射到同一自然人。这是来源间身份关系的证据，尚未决定前端唯一人物卡应显示哪段职位，也未核实三个 PTR 之间是否有修订或重复关系。

后续安全实现需要一个**明确来源绑定的任职历史/人物 crosswalk**：将三个 OGE 文档键关联到稳定人物身份，同时在每份申报上保留当时的机构、职位及原始目录字段；分别核对报告修订/重复关系、候选行其他资格和完整前端人物语义，再生成新候选。不得直接把任一职位哈希 ID 改给另一任职，或只因姓名相同放开全目录规则。当前 77 行继续隔离，`projection_ready=false`。
