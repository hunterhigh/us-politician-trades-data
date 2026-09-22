# 白宫 Form 201 请求型报告受影响人物清单

执行状态更新：用户已决定不提交Form 201或白宫请求表。本文保留为历史OGE目录差异审计，不是申请队列或当前获取路径；相应人物应先按白宫官网公开原件逐份核查，官网未列或无法核验的报告明确保留缺口。

核对日期：2026-09-22。依据 2026-09-21 已归档 OGE 目录，目录原页 SHA-256：`1488a6d13396247228918a1021fc4395056c24a5bae4856dab2c7e1a20ec3896`；对应 [生产 review 请求清单](https://github.com/hunterhigh/us-politician-trades-data/blob/review/oge/whitehouse/request-plan-current.json)。本文只统计**年度 278e 与 278‑T** 的请求型报告受影响姓名，**未统计新人入职、离任及其他 278e 类型**；它不是全部 Form 201 需求、白宫完整人员名册，也不是已取得的独立报告份数。完整范围的取舍见[白宫披露补全与前端验收计划](白宫披露补全与前端验收计划.md)。

**重要更正**：本文“请求型”仅指 OGE 目录入口。核对[白宫官网公开披露页面](https://www.whitehouse.gov/disclosures/)后，发现其中直接公开了许多本清单人物的年度、入职及定期交易原件，包括 Wiles、Miller、Scavino。不得把这 152 个姓名字符串直接称为“不做 Form 201 就无法获取资料的人”；真正的无申请缺口必须以白宫官网与 OGE 直链逐份交叉核对后计算。本文目前只适合作为 OGE 入口差异审计，不是优先申请名单。

范围：`White House Office` 与 `Office of the Vice President`（目录大小写变体合并）。326 条请求型目录出现项汇总为 245 组申请意图，涉及 **152 个不同的目录姓名字符串**；白宫办公室目录姓名 138 个，副总统办公室目录姓名 15 个，其中 Michael C. Donilon 两处均出现，按完整姓名字符串去重后为 152 个。这不等于已核定 152 位独立自然人，例如 `Flaherty, Robert N`/`Flaherty, Robert R`、`Raghavan, Gautam`/`Raghavan, Gautam N` 仍须身份核验，不能自行合并。所有请求状态仍是 `not_submitted`，**这些请求型报告均未进入正式 `main`**。

**重点与部署**：冻结前端明确要求的初始重点人物是 Donald Trump 与 Nancy Pelosi，二者不在本请求型清单；特朗普有直链 278-T/年报待解析，Pelosi 的 House 身份已部署。项目计划增加的 JD Vance 也属于直链年报，不在本请求型清单。正式 `main` 目前没有白宫办公室或副总统办公室身份/交易/持仓；下列“建议优先”7 人是基于目录所列职务提出的资料取得顺序，**不是前端契约硬性名单，也不证明他们现在仍任该职**。

“近年”仅指此人至少有一条请求型目录记录在 2025-01-20 或之后入库；“历史”仅按目录入库时间分组，**不能据此推断任职起止**。年度括号年份是 OGE 目录标签，不等同资产报告年度；278-T 括号年份只是目录入库年，不是逐笔交易日期。职务为该人的最新目录记录原文，可能是前任职务。

## 近年白宫目录（24 人）

| 人物 | 请求型报告 | 目录所列职务 | 优先级 |
|---|---|---|---|
| Brasseaux, Matthew | 年度278e 2026 | Director of the Office of Political Affairs | 常规 |
| Bresso, Gineen M | 278-T（目录年 2025,2026） | Deputy Counsel to the President | 常规 |
| Burrows, Daniel | 278-T（目录年 2026） | Deputy Staff Secretary | 常规 |
| Gabriel, Robert | 年度/离任合并（目录年 2026） | Assistant to the President for Policy | 常规 |
| Gor, Sergio | 278-T（目录年 2025） | Director of the Office of Presidential Personnel | 建议优先 |
| Harrison, Hayley | 年度278e 2026 | Chief of Staff for the Officeof the First Lady | 常规 |
| Harrison, William B | 年度278e 2026 | Deputy Chief of Staff for Operations | 常规 |
| Hassett, Kevin A | 年度278e 2026 | Director of the National Economic Council | 建议优先 |
| Hayes, Sean | 年度278e 2026 | Deputy Assistant to President & Deputy WH Counsel | 常规 |
| Kenny, Stephen | 278-T（目录年 2025,2026） | Deputy Counsel to the President | 常规 |
| Klopp, Jacalynne B | 278-T（目录年 2026） | Deputy Assistant to President & Advisor | 常规 |
| Lawkowski, Gary | 年度278e 2026 | Deputy Assistant to the President & Deputy Counsel to the President | 常规 |
| McMullan, Matthew | 278-T（目录年 2026） | Deputy Director of Legislative Affairs | 常规 |
| Miller, Stephen | 278-T（目录年 2025） | Deputy Chief of Staff for Policy and Homeland Security Advisor | 建议优先 |
| Overton, Heidi | 年度278e 2026 | Deputy Assistant to the President for Domestic Policy | 常规 |
| Policastro, Marie K | 年度278e 2026 | Deputy Assistant to President & Director of Scheduling | 常规 |
| Sayle, Desiree T | 年度278e 2026 | Director of Presidential Correspondence | 常规 |
| Scavino, Dan J | 278-T（目录年 2025） | Deputy Chief of Staff | 建议优先 |
| Scharf, William O | 278-T（目录年 2026） | Staff Secretary | 常规 |
| Sikma, James B | 年度278e 2026 | Deputy Assistant to President for Domestic Policy Council | 常规 |
| Underwood, Emily A | 年度278e 2026 | Deputy Assistant to the President & Senior Policy Strategist | 常规 |
| Waltz, Michael | 年度278e 2025 | Assistant to President & National Security Advisor | 建议优先 |
| Warrington, David | 278-T（目录年 2025） | Assistant to the President & Counselor to the President | 建议优先 |
| Wiles, Susie | 278-T（目录年 2025） | Assistant to the President & Chief of Staff | 建议优先 |

## 副总统办公室历史目录（15 人）

| 人物 | 请求型报告 | 目录所列职务 | 优先级 |
|---|---|---|---|
| Brooks, Jordan A | 年度278e 2023 | Deputy Assistant to the President & Chief of Staff to the Second Gentleman | 常规 |
| Chang, Aaron C | 278-T（目录年 2020） | Deputy Assistant to President & Director of Advance to Vice President | 常规 |
| Donilon, Michael C | 年度278e 2022,2023 | Special Advisor to Vice President for Communications | 常规 |
| Flournoy, Hartina N | 年度/离任合并（目录年 2022） | Assistant to the President & Chief of Staff to the Vice President | 常规 |
| Gordon, Philip H | 年度278e 2022,2023,2024 | Assistant to the President & National Security Advisor to the Vice President | 常规 |
| Jacob, Gregory F | 278-T（目录年 2020） | Counsel to Vice President | 常规 |
| Kellogg, Joseph K | 年度278e 2020；278-T（目录年 2020） | National Security Adviser to the Vice President | 常规 |
| Kosoglu, Rohini | 年度278e 2022 | Assistant to the President & Domestic Policy Advisor to the Vice President | 常规 |
| Lucius, Kristine | 年度278e 2024；278-T（目录年 2024） | Deputy Assistant to the President & Domestic Policy Advisor | 常规 |
| Parikh, Chirag R | 年度278e 2022,2023,2024 | Executive Secretary | 常规 |
| Simmons, Jamal | 278-T（目录年 2023） | Communications Director | 常规 |
| Songer, Erica K | 年度278e 2023,2024 | Counsel to the Vice President & Deputy Assistant to the President | 常规 |
| Voles, Lorraine A | 年度278e 2023,2024；278-T（目录年 2024,2025） | Assistant to the President & Chief of Staff to the Vice President | 常规 |
| Wilson, Erin S | 年度278e 2023,2024 | Deputy Chief of Staff to the Vice President & Deputy Assistant to the President | 常规 |
| Young, Stephanie L | 年度/离任合并（目录年 2025） | Deputy Assistant to the President & Senior Advisor to the Vice President | 常规 |

## 其他白宫历史目录（113 人）

| 人物 | 请求型报告 | 目录所列职务 | 优先级 |
|---|---|---|---|
| Adiga, Mala | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Director of Policy & Projects for the First Lady | 常规 |
| Alexander, Elizabeth | 年度278e 2022,2024；年度/离任合并（目录年 2023） | Deputy Assistant to the President & Communications Director for the First Lady | 常规 |
| Anello, Russell M | 年度278e 2024；278-T（目录年 2024） | Deputy Counsel to the President | 常规 |
| Aron-Dine, Aviva | 年度278e 2023 | Deputy Assistant to the President & Deputy Director of the National Economic Counsel | 常规 |
| Bains, Chiraag | 年度278e 2022 | Deputy Assistant to the President for Racial Justice and Equity | 常规 |
| Bedingfield, Katherine J | 年度278e 2022 | Assistant to President & Director of Communications | 常规 |
| Benjamin, Stephen | 年度278e 2024；278-T（目录年 2024） | Assistant to the President, Director Office of Public Engagement | 常规 |
| Bernal, Anthony | 年度278e 2022,2023,2024 | Assistant to the President & Senior Advisor to the First Lady | 常规 |
| Brainard, Lael | 年度278e 2023,2024 | Director of the National Economic Council | 常规 |
| Cheng, Dennis W | 278-T（目录年 2024） | Deputy Assistant to the President & Deputy Director of Political Strategy and Outreach | 常规 |
| Cipollone, Pasquale A | 年度278e 2020；278-T（目录年 2020,2021） | Assistant to the President | 常规 |
| Citron, Jamison R. | 年度278e 2024 | Deputy Assistant to the President & Principal Deputy Director of the Office of Public Engagement | 常规 |
| Conley, Danielle Y | 年度278e 2022 | Deputy Assistant to the President & Deputy Counsel to the President | 常规 |
| Costa, Kristina L | 年度278e 2023,2024 | Deputy Assistant to the President for Clean Energy Innovation & Implementation | 常规 |
| Dalton, Olivia A | 年度278e 2023 | Deputy Assistant to the President & Principal Deputy Press Secretary | 常规 |
| Danaher, Brendan J | 年度278e 2024 | Deputy Assistant to the President & Deputy Director for Labor at the National Economic Council | 常规 |
| Deese, Brian | 年度278e 2022 | Assistant to the President & Director of the NEC | 常规 |
| Delery, Stuart F | 年度278e 2022,2023 | Deputy Assistant to the President & Deputy Counsel to the President | 常规 |
| Dodin, Reema | 年度278e 2022,2023 | Deputy Assistant to President & Deputy Director of Legislative Affairs | 常规 |
| Drake, Celeste | 年度278e 2023 | Deputy Assistant to President & Deputy Director National Economic Council | 常规 |
| Dunn, Anita B | 年度278e 2023,2024；278-T（目录年 2023） | Assistant to President & Senior Advisor | 常规 |
| Eichner, Stacy L | 年度278e 2024 | Deputy Assistant to the President & Deputy Director for the Office of Presidential Personnel | 常规 |
| Eisenberg, John A | 年度278e 2020 | Deputy Assistant to President & NSC Legal Advisor & Deputy Counsel to President for National Security Affairs | 常规 |
| Elizondo, Carlos | 年度278e 2022,2023,2024 | Deputy Assistant to President & White House Social Secretary | 常规 |
| Fazili, Sameera | 年度278e 2022 | Deputy Assistant to the President & Deputy Director of the National Economic Counsel | 常规 |
| Feldman, Stefanie | 年度278e 2022,2023,2024 | Assistant to the President & Staff Secretary; Director, Office of Gun Violence Prevention | 常规 |
| Filipic, Anne | 年度278e 2022；278-T（目录年 2021） | Assistant to the President, Director of Management and Administration | 常规 |
| Finer, Jonathan | 年度278e 2022,2023,2024 | Assistant to the President & Principal Deputy National Security Advisor | 常规 |
| Flaherty, Robert N | 年度278e 2023 | Deputy Assistant to the President & Director of Digital Strategy | 常规 |
| Flaherty, Robert R | 年度278e 2022 | Deputy Assistant to the President & Director of Digital Strategy | 常规 |
| Geltzer, Joshua A | 年度278e 2024 | Deputy Assistant to the President, Deputy White House Counsel, & NSC Legal Advisor | 常规 |
| Gilmartin, Kayleigh | 278-T（目录年 2020） | Press Secretary | 常规 |
| Goff, Shuwanza | 年度278e 2022,2024 | Assistant to the President & Director of the White House Office of Legislative Affairs | 常规 |
| Gordon, Robert | 年度278e 2024 | Deputy Assistant to the President | 常规 |
| Grigsby, Stacey K | 年度278e 2023；278-T（目录年 2022） | Deputy Counsel to the President | 常规 |
| Harris, Seth | 年度278e 2022 | Deputy Assistant to President & Deputy Director National Economic Council | 常规 |
| Hornung, Daniel Z | 年度278e 2024 | Deputy Assistant to the President & Deputy Director of the National Economic Counsel | 常规 |
| Jean-Pierre, Karine | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Principal Deputy Press Secretary | 常规 |
| Jha, Ashish K | 年度/离任合并（目录年 2023） | Coordinator of the COVID-19 Response and Counselor to the President | 常规 |
| Jones, Meredith A | 年度278e 2024 | Deputy Assistant to the President & Deputy Director of Legislative Affairs & House Liaison | 常规 |
| Kamin, David | 年度278e 2022 | Deputy Assistant to the President & Deputy National Climate Advisor | 常规 |
| Kaplan, Jennifer Y | 年度278e 2024 | Deputy Assistant to the President & Senior Advisor to the Director of the Office of Public Engagement | 常规 |
| Keith, Katherine M | 年度278e 2024 | Deputy Assistant to the President & Deputy Director of the Gender Policy Council | 常规 |
| Killin, Jessica | 年度278e 2024；278-T（目录年 2024） | Deputy Assistant to the President | 常规 |
| Klain, Ronald A | 年度278e 2022 | Assistant to the President & Chief of Staff | 常规 |
| Klein, Jennifer | 年度278e 2022,2023,2024；278-T（目录年 2021,2022,2024） | Deputy Assistant to the President & Co-Chair and Executive Director, Gender Policy Council | 常规 |
| Koh, Daniel | 年度278e 2024；278-T（目录年 2023,2024,2025） | Deputy Director Intergovernmental Affairs | 常规 |
| LaBolt, Benjamin C | 年度278e 2024,2025；278-T（目录年 2024,2025） | Assistant to the President | 常规 |
| Lambert, Tericka M | 年度278e 2024 | Deputy Director | 常规 |
| Landrieu, Mitchell | 年度278e 2023 | Senior Advisor to the President | 常规 |
| Lawrence, Elisabeth H | 年度278e 2023,2024 | Deputy Assistant to the President for Immigration | 常规 |
| Levy, Scott | 年度278e 2024 | General Counsel | 常规 |
| Lichter, Jennifer B | 278-T（目录年 2020） | Deputy Assistant to President for Domestic Policy Council | 常规 |
| Liddell, Christopher | 278-T（目录年 2020,2021） | Assistant to President & Director of Strategic Initiatives | 常规 |
| Martin, Carmel | 年度278e 2022,2023；278-T（目录年 2021,2022,2023） | Deputy Assistant to the President & Deputy Director of the DPC for Economic Mobility | 常规 |
| McCarthy, John W | 年度278e 2024 | Deputy Assistant to the President & Senior Advisor for Political Engagement | 常规 |
| McCarthy, Regina A | 年度278e 2022；278-T（目录年 2021,2022） | Assistant to the President & National Climate Advisor | 常规 |
| Meadows, Mark | 278-T（目录年 2020） | Chief of Staff | 常规 |
| Montoya, Ryan | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Director of Scheduling and Advance | 常规 |
| Moritsugu, Erika L | 年度278e 2022,2023,2024 | Deputy Assistant to the President & AA & NHPI Senior Liaison | 常规 |
| Nevins, Kristan K | 278-T（目录年 2020） | Cabinet Secretary | 常规 |
| Niceta, Anna C | 年度278e 2020 | Deputy Assistant to President & White House Social Secretary | 常规 |
| Noble, David L | 年度278e 2023,2024 | Assistant to the President for Management and Administration & Director of the Office of Administration | 常规 |
| Nouri, Ali | 年度278e 2024；278-T（目录年 2023,2024） | Deputy Assistant to the President | 常规 |
| O'Brien, Robert C | 年度278e 2020 | Assistant to President & National Security Advisor | 常规 |
| O'Malley Dillon, Jennifer B | 年度278e 2022,2023 | Assistant to the President & Deputy Chief of Staff | 常规 |
| Orthman, Kristen D | 年度278e 2024 | Deputy Assistant to the President & Principal Deputy Communications Director | 常规 |
| Perez, Tom | 年度278e 2024 | Senior Advisor & Director of Office of Intergovernmental Affairs | 常规 |
| Petrelius, Katherine N | 年度278e 2023 | Deputy Assistant to President & Deputy Director of the Presidential Personnel Office | 常规 |
| Philbin, Patrick F | 年度278e 2020 | Deputy Counsel to the President | 常规 |
| Phillips, John R | 年度278e 2023 | Deputy Assistant to President & NSC Legal Advisor & Deputy Counsel to President for National Security Affairs | 常规 |
| Podesta, John D | 年度278e 2023,2024 | Senior Advisor to the President for Clean Energy Innovation & Implementation | 常规 |
| Pottinger, Matt | 年度278e 2020；278-T（目录年 2020,2021） | Assistant to President & Deputy National Security Advisor | 常规 |
| Psaki, Jennifer R | 年度/离任合并（目录年 2022） | Assistant to the President & White House Press Secretary | 常规 |
| Quillian, Natalie H | 年度278e 2024 | Assistant to the President & Deputy Chief of Staff | 常规 |
| Raghavan, Gautam | 年度278e 2023,2024 | Assistant to the President & Director of Presidential Personnel | 常规 |
| Raghavan, Gautam N | 年度278e 2022 | Deputy Assistant to President & Deputy Director of the Presidential Personnel Office | 常规 |
| Ramamurti, Bharat N | 年度278e 2023；278-T（目录年 2023） | Deputy Assistant to the President & Deputy Director of the National Economic Counsel | 常规 |
| Reddy, Vinay | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Director of Speechwriting | 常规 |
| Reed, Bruce | 年度278e 2022,2023,2024 | Assistant to the President & Deputy Chief of Staff | 常规 |
| Remus, Dana A | 年度278e 2022 | Assistant to the President & White House Counsel | 常规 |
| Repko, Mary F | 年度278e 2024 | Deputy National Climate Advisor & Deputy Assistant to the President | 常规 |
| Reynolds, Lindsay B | 年度/离任合并（目录年 2020） | Assistant to President & Chief of Staff to First Lady | 常规 |
| Reynoso, Julissa | 278-T（目录年 2021） | Assistant to the President & Chief of Staff to Dr. Jill Biden | 常规 |
| Ricchetti, Steven J | 年度278e 2022,2023,2024 | Assistant to the President & Counselor to the President | 常规 |
| Rice, Susan E | 年度278e 2022,2023；278-T（目录年 2021） | Assistant to the President & Domestic Policy Advisor | 常规 |
| Richmond, Cedric | 年度/离任合并（目录年 2022） | Senior Advisor | 常规 |
| Rodriguez, Julie | 年度278e 2022,2023 | Deputy Assistant to the President & Director of Intergovernmental Affairs | 常规 |
| Rollins, Brooke L | 年度278e 2020；278-T（目录年 2020） | Assistant to President for Strategic Initiatives | 常规 |
| Ruffner, Richard | 278-T（目录年 2024） | Assistant to the President & Director of Oval Office Operations | 常规 |
| Ruiz, Emma N | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Director of Political Strategy and Outreach | 常规 |
| Ryan, Evan M | 年度278e 2022,2023,2024 | Assistant to President & Cabinet Secretary | 常规 |
| Sauber, Richard | 年度278e 2023；278-T（目录年 2022,2023,2024） | Special Counsel to President | 常规 |
| Sherwood-Randall, Elizabeth | 年度278e 2022,2023,2024；278-T（目录年 2021） | Assistant to the President for Homeland Security | 常规 |
| Siddique, Zayn N | 年度278e 2023 | Deputy Assistant to the President for Economic Mobility | 常规 |
| Siskel, Edward | 年度278e 2024；278-T（目录年 2023） | White House Counsel | 常规 |
| Slater, Lee | 年度278e 2024 | Deputy Assistant to the President & Deputy Director | 常规 |
| Slevin, Chris | 年度278e 2022 | Deputy Assistant to President for Legislative Affairs | 常规 |
| Stevenson, Patrick J | 年度278e 2024 | Deputy Assistant to the President & Senior Advisor for Digital Strategy | 常规 |
| Stone, Roger | 278-T（目录年 2020） | Deputy Assistant to President & Director of White House Information Technology | 常规 |
| Su, Jonathan C | 年度278e 2022 | Deputy Assistant to the President & Deputy Counsel to the President | 常规 |
| Sullivan, Jacob | 年度278e 2022,2023,2024；278-T（目录年 2022） | Assistant to the President & National Security Advisor | 常规 |
| Tanden, Neera | 年度278e 2023,2024 | Staff Secretary & Senior Advisor to the President | 常规 |
| Terrell, Louisa | 年度278e 2022,2023 | Assistant to the President & Director of the White House Office of Legislative Affairs | 常规 |
| Thompson, Karl | 年度278e 2023,2024；278-T（目录年 2022） | Deputy Counsel to the President | 常规 |
| Tom, Christian | 年度278e 2024；278-T（目录年 2024,2025） | Assistant to the President & Director of Digital Strategy | 常规 |
| Tomasini, AnnMarie | 年度278e 2022,2023,2024 | Assistant to the President & Director of Oval Office Operations | 常规 |
| Wallace, Andrew | 278-T（目录年 2024） | Deputy Assistant to the President & Deputy Director of Legislative Affairs & Senate Liaison | 常规 |
| Williams, Ashley N | 年度278e 2024；278-T（目录年 2024） | Deputy Assistant to the President & Senior Advisor to the President & Director of Strategic Outreach | 常规 |
| Wold, Theodore J | 278-T（目录年 2021） | Deputy Assistant to President for Domestic Policy Council | 常规 |
| Young, Christen L | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Deputy Director of the DPC for Health & Veterans Affairs | 常规 |
| Zaidi, Ali | 年度278e 2022,2023,2024 | Deputy Assistant to the President & Deputy National Climate Advisor | 常规 |
| Zients, Jeffrey D | 年度278e 2024 | Chief of Staff | 常规 |

## 直链报告中的 4 人（不受 Form 201 这一缺口直接影响）

- Donald J. Trump：直链年度 278e 与 278-T；原件已取得一部分，合格白宫事实尚未发布。
- JD Vance：直链年度 278e；2026 年发布的原件已归档，合格持仓尚未发布。
- Joseph R. Biden、Kamala D. Harris：目录中有历史年度直链，原件仍待取得；不能据此推断当前政府持仓。

若不提交 Form 201，应先逐份核查白宫官网直接公开的对应原件；只有两个官方公开入口都未找到并完成来源对账的项目，才列为“无申请原件缺口”。某人没有 278-T 目录项，不等于其没有交易；OGE 只要求发生应申报交易时提交 278-T。[OGE 说明](https://www.oge.gov/web/278eGuide.nsf/Form_278-T)。
