# 万斯 2025 年度 278e Part 7 与 278-T 影子审计

本审计只使用固定 [OGE 官方年报](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/40CE0F66F853096985258E27002DDFBB/$FILE/JD-Vance-2026-278ANNUAL.pdf) 的 17 页原件（SHA-256 `bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d`）、固定[白宫发布副本](https://www.whitehouse.gov/wp-content/uploads/2026/06/Vice-President-JD-Vance-2025-Annual-Report.pdf)（SHA-256 `d43f25659a26474faae4df8218ff352e3c01a2eaf17b6ae35ae07649bbe90c3d`）、[白宫公开目录](https://www.whitehouse.gov/disclosures/)的固定页，以及固定 `main`、`review` 候选。来源与十条可见交易的机器输入见[证据清单](../backend/tests/fixtures/oge_annual_vance_2026_part7_evidence.json)，逐条机器结果见[影子审计](../backend/tests/fixtures/oge_annual_vance_2026_part7_audit.json)。与最新 HTML 基准冲突时仍以用户给定 HTML 为准；此审计没有改收益值语义或前端文件。

## 日期证据

OGE 与白宫副本封面显示同一组手写日期。OGE 原件第 1 页可辨认：申报人**签字认证** `2026-06-23`；机构伦理审查 `2026-06-25`；页脚 `OGE Received 6/29/2026`；OGE 认证 `2026-06-30`。这些分别是签名、审查、OGE 收件和认证事件。原件没有可单独辨认且语义明确的**向机构提交时间戳**，所以机器审计 `annual_report_filing_date=null`。固定白宫 `main` 持仓的 `filed_at=2026-06-23` 与签名日相同，不能据此把该日期再次推定为法律意义上的实际提交日。Part 7 十条交易共享这份年报的日期证据，交易行本身未另载报告提交日。

## 逐行状态

第 11 页独立目视数得 1–10 行填写、11–20 模板空白；固定 opt-in v2 抽取十条与可见资产、方向、交易日、金额区间逐行一致。第 7–9 行虽然均为 DIA 和相同金额区间，交易日分别为 7 月 14 日、10 月 14 日、12 月 15 日，是三条不同的年报印刷行；不能因名称相同互相消重。

| 行 | 资产 | 交易日 | 方向 | 金额区间 | 固定候选匹配 | 官方 278-T 重复关系 |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | Vanguard 2034/2035 | 2025-03-20 | 买 | $1,001–$15,000 | 0 | 未闭合 |
| 2 | Vanguard 2040 | 2025-03-20 | 买 | $1,001–$15,000 | 0 | 未闭合 |
| 3 | Vanguard 2038 | 2025-04-29 | 买 | $15,001–$50,000 | 0 | 未闭合 |
| 4 | QQQ | 2025-06-27 | 买 | $250,001–$500,000 | 0 | 未闭合 |
| 5 | DIA | 2025-06-27 | 买 | $500,001–$1,000,000 | 0 | 未闭合 |
| 6 | SPY | 2025-06-27 | 买 | $500,001–$1,000,000 | 0 | 未闭合 |
| 7 | DIA | 2025-07-14 | 买 | $1,001–$15,000 | 0 | 未闭合 |
| 8 | DIA | 2025-10-14 | 买 | $1,001–$15,000 | 0 | 未闭合 |
| 9 | DIA | 2025-12-15 | 买 | $1,001–$15,000 | 0 | 未闭合 |
| 10 | Rise of the Rest Seed Fund, LP | 2025-01-16 | 卖 | $100,001–$250,000 | 0 | 未闭合 |

`0` 仅表示固定 `main=05391900bd35e5a828cd374c790991b2dc7a12c7` 人物分片和 `review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` 候选中，Vance 已记录交易数均为零。固定白宫目录页面 SHA-256 `80441b3dd909ca549d7340715824df83fc22fc7fdeb615dacd881620d60a364f` 列 Vance 两份 278e 链接，没有 Vance 278-T 链接。目录与候选都不能证明 OGE、机构或未来公开渠道的 278-T 已穷尽。OGE 的[278-T 指引](https://oge.gov/web/278eGuide.nsf/Form_278-T)也说明只有可报告交易才需提交，不能由目录未见一份报告推断交易一定未单独申报。

因此十行的 `official_278t_duplicate_status=unresolved`、`official_filing_date_status=unverified`、`shadow_disposition=quarantined`；生产可晋级数 **0**。年报 278e 与可能存在的 278-T 是否重复、订正或仅部分重合，仍需完整官方目录或对应原件、版本链和字段级比对。两渠道年报可见内容高度相近，不等于 278-T 不存在。最新 HTML 的七个无法计算收益值继续显示“—”，本审计不产生收益率。

本分支实际运行了 `python -m unittest discover -s backend/tests -p 'test_oge_annual*.py' -v`；官方封面与第 11 页的手写/印刷内容由固定 PDF 渲染目视复核。CI 中的 JSON 测试验证来源绑定和状态保持，不声称自动识别手写日期或穷尽线上 278-T。
