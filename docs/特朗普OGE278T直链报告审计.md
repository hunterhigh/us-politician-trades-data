# 特朗普 OGE 278‑T 直链报告证据审计

审计日期：2026-09-22。固定读取公开生产 `review` commit `b230027fa11c9cf87a8c2f411309d8c7ad35d8b2`，对照 `evidence` commit `99df227982f347ed40eae4de37c0e584a25177a1` 的完整 Git 树。此文只记录当前已归档、已解析的直链 278‑T；不把目录日期、PDF 文件名日期或 OGE 收件日期冒充申报日期，也不包含 Form 201 请求型原件或年度 278e。

证据入口：`oge/discoveries/1488a6d13396247228918a1021fc4395056c24a5bae4856dab2c7e1a20ec3896.json` 为 OGE 官方目录快照；`oge/extractions/<document_id>/<source_sha256>/oge-278t-pdf-v2.json` 为当前解析输出；`oge/qualifications/current.json` 为候选资格审计。每一份解析输出的官方 URL 与目录一致，`source_sha256` 对应的 PDF 和元数据均存在于上述 `evidence` commit；18 份逐份交叉检查无缺失。下表中的报告编号可点击直达官方 PDF。

| 官方报告 ID | 目录加入日 | 解析器暂存行 | 提取时隔离行 | 候选门禁隔离行 | 额外障碍 |
|---|---|---:|---:|---:|---|
| [2bf91f890f718acb85258e5b002de16b](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/2BF91F890F718ACB85258E5B002DE16B/$FILE/Donald-J-Trump-08.12.2026-278T.pdf) | 2026-08-22 | 0 | 0 | 0 | 表格未识别 |
| [f9ca13b970439e8f85258e27002ddf15](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/F9CA13B970439E8F85258E27002DDF15/$FILE/Donald-J-Trump-06.25.2026-278T%20(2).pdf) | 2026-07-01 | 60 | 991 | 1051 | 扫描表格需逐格核验 |
| [ac43530823bd60d485258e27002ddef7](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/AC43530823BD60D485258E27002DDEF7/$FILE/Donald-J-Trump-06.25.2026-278T.pdf) | 2026-07-01 | 146 | 1068 | 1214 | 扫描表格需逐格核验 |
| [5326d3af5be7c25385258df7002dd1b7](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/5326D3AF5BE7C25385258DF7002DD1B7/$FILE/Trump%2C%20Donald%20J.-05.08.2026-278T.pdf) | 2026-05-14 | 0 | 62 | 62 | 扫描表格需逐格核验 |
| [405e4ec4e27be8d185258df7002dd1c0](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/405E4EC4E27BE8D185258DF7002DD1C0/$FILE/Trump%2C%20Donald%20J.-05.08.2026-278T(2).pdf) | 2026-05-14 | 191 | 3443 | 3634 | 扫描表格需逐格核验 |
| [cd75555856a7d2e485258de4002dd4a0](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/CD75555856A7D2E485258DE4002DD4A0/$FILE/Donald-J-Trump-4.20.2026-278T.pdf) | 2026-04-25 | 1 | 174 | 175 | 扫描表格需逐格核验 |
| [bcf60d94b8f1e59285258db000347f61](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/BCF60D94B8F1E59285258DB000347F61/$FILE/Donald%20J.%20Trump%202.26.2026%20278-T%20(2).pdf) | 2026-03-04 | 0 | 0 | 0 | 表格未识别 |
| [174165f6e1e120b185258db000347f54](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/174165F6E1E120B185258DB000347F54/$FILE/Donald%20J.%20Trump%202.26.2026%20278-T%20(1).pdf) | 2026-03-04 | 0 | 0 | 0 | 表格未识别 |
| [268353939b7dacb585258d81003471b1](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/268353939B7DACB585258D81003471B1/$FILE/Donald-J-Trump%201.14.2026-278T.pdf) | 2026-01-16 | 0 | 0 | 0 | 表格未识别 |
| [87bfe542a2751e0285258d6600346fcd](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/87BFE542A2751E0285258D6600346FCD/$FILE/Donald%20J.%20Trump%2012.18.2025%20278-T.pdf) | 2025-12-20 | 0 | 0 | 0 | 表格未识别 |
| [903a217dc18563ec85258d4a0031b044](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/903A217DC18563EC85258D4A0031B044/$FILE/Donald%20J.%20Trump%2011.14.2025%20278-T.pdf) | 2025-11-22 | 0 | 0 | 0 | 表格未识别 |
| [aa799a2729b4d1be85258d430031a320](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/AA799A2729B4D1BE85258D430031A320/$FILE/Donald%20J.%20Trump%2010.17.2025%20278-T.pdf) | 2025-11-15 | 0 | 0 | 0 | 表格未识别 |
| [2c89623dc721f62585258d430031a32a](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/2C89623DC721F62585258D430031A32A/$FILE/Donald%20J.%20Trump%2010.20.2025%20278-T.pdf) | 2025-11-15 | 0 | 0 | 0 | 表格未识别 |
| [18353894fe440b3685258d430031a337](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/18353894FE440B3685258D430031A337/$FILE/Donald%20J.%20Trump%2010.20.2025%20278-T%20(2).pdf) | 2025-11-15 | 0 | 0 | 0 | 表格未识别 |
| [322b8a28db21cc9285258cfd002c0d0b](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/322B8A28DB21CC9285258CFD002C0D0B/$FILE/Donald%20J.%20Trump%209.3.25%20278-T.pdf) | 2025-09-06 | 0 | 0 | 0 | 表格未识别 |
| [e9c024e25c9e4b2085258ceb006e7e23](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/E9C024E25C9E4B2085258CEB006E7E23/$FILE/Donald-J-Trump-08.12.2025-278T.pdf) | 2025-08-19 | 107 | 400 | 507 | 扫描表格需逐格核验 |
| [5315a095a2ee1b9185258ceb006e7e36](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/5315A095A2EE1B9185258CEB006E7E36/$FILE/Donald-J-Trump-08.12.2025-278T(3).pdf) | 2025-08-19 | 0 | 0 | 0 | 表格未识别 |
| [024d876aae518c5085258ceb006e7e2e](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/024D876AAE518C5085258CEB006E7E2E/$FILE/Donald-J-Trump-08.12.2025-278T(2)%20AMENDED.pdf) | 2025-08-19 | 0 | 0 | 0 | 无可解析交易行；修订关系未闭合 |

**当前结果：18 份全部 `filed_at=null`，候选晋级 0 笔。** 解析器暂存 505 行、提取时隔离 6,138 行；资格门禁把两类共 6,643 行均隔离。所有报告均有 `filer_signature_date_not_unique`，候选审计相应记录 `document_evidence_incomplete` 与 `filed_at_invalid`；其中 11 份还未识别交易表，1 份带未解析的 amended 关系。暂存行不是已核验交易。

人工观察固定原件样本（仅用于确定解析失败原因，不写入候选事实）：2025-08-12 [报告](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/E9C024E25C9E4B2085258CEB006E7E23/$FILE/Donald-J-Trump-08.12.2025-278T.pdf) 第 1 页的申报者签署日是手写，PDF 文本层只给出后续 OGE 收件日；其第 2 页第 9 行的解析 `asset_name` 把实际第 9、10 两行粘连为一条。2026-06-25 的 [报告一](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/AC43530823BD60D485258E27002DDEF7/$FILE/Donald-J-Trump-06.25.2026-278T.pdf) 与 [报告二](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/F9CA13B970439E8F85258E27002DDF15/$FILE/Donald-J-Trump-06.25.2026-278T%20(2).pdf)，以及 [2026-08-12 报告](https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/2BF91F890F718ACB85258E5B002DE16B/$FILE/Donald-J-Trump-08.12.2026-278T.pdf) 的第 1 页也采用手写申报者日期。单独把目录或收件日期补进 `filed_at`，会把粘连行等不可靠 OCR 结果误晋级。

后续自动化必须先在这些官方原件上建立扫描表格的页面/列界、逐格 OCR 置信度、连续行号与页数闭合、签署日期可核验提取，以及修订关系规则；不满足的行继续保留现有隔离。此次审计未放宽解析器或候选门禁。
