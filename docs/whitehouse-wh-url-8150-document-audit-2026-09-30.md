# 固定候选中 8,150 条 `wh-url` 交易的文档级审计

本次只读使用固定 `review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` 的 `candidates/disclosure-current.json`（Git blob `36cabab1f0c02a982cd4b04f09ced0d33d270352`；本地文件 SHA-256 `7bdd6b042b86088733b2dc13e57d59cb21743905adccf42df83276d9470415ba`）、同一提交的 `whitehouse/disclosures/index-current.json`（Git blob `0241a7116ca4efd765e14c1e5ff33ccfd0b9d0ca`）、固定 `evidence=5fe5f0adb3181c5d1552dff3f07336464c3043a7` 的路径树，以及 `review` 的抽取路径树。机器结果是 [19 文档清单](../backend/tests/fixtures/whitehouse_wh_url_8150_document_audit.json)；[只读重放脚本](../backend/scripts/audit_wh_url_candidate.py)核对本地候选/目录字节确属指定提交，再计算逐文档计数。

| 文档分层 | 文档 | 候选交易行 | 官方白宫公开归档 | 本次关系判断 |
| --- | ---: | ---: | --- | --- |
| 278e Annual | 1 | 6,759 | 9 个按序分片加元数据，单一 PDF SHA 路径 | 年报交易；需与 PTR 独立核对 |
| 278-T | 18 | 1,391 | 各有一份 PDF 加元数据 | 各自独立文档；与 OGE 目录身份未证实 |
| 合计 | 19 | 8,150 | 19/19 在固定白宫目录、`evidence` 路径和 `review` 抽取路径可定位 | 不增删正式候选 |

19 份候选文档的 `wh-url` ID 与白宫[公开目录](https://www.whitehouse.gov/disclosures/)的文档 ID/URL 一对一匹配，且每文档候选行只指向一个人物。路径树中每文档只有一个由归档路径记录的 PDF SHA-256；这证明冻结仓库中的**路径和文档身份绑定**。本轮未重新读取全部 19 份 PDF 的字节来独立重算哈希，尤其大年报按 9 份二进制片归档，故不把路径检查称为 PDF 内容重放。每份文档均有对应抽取文件路径。

固定候选中，与这 8,150 行不同来源的 OGE 交易有 5,079 行。以同一人物、规范化资产名、交易日、方向、金额上下限组成的**严格候选键**比较，`wh-url` 对 OGE 候选的相同键为 **0**。这只是当前已取得候选的字段比较，不能证明官方 OGE 278-T 不重叠或其报告不存在。数量最大的 Trump 白宫组为 8,067 行，固定 OGE 候选中该人物另有 383 行，交易日全在 2026 年 7 月；此白宫组交易日最晚 2026 年 6 月 29 日，因此在这个固定候选里没有同日交易可比。其余六位白宫人物在当前 OGE 候选中没有对应行，也不是全面覆盖的证据。

年报 6,759 行与白宫 278-T 1,391 行之间，严格候选键相同数同样为 **0**；去掉资产名后，有 **361 条年报行**与某 PTR 行共享人物、交易日、方向和金额区间。这 361 条只能列作**可能比对对象**，同一天同金额多笔交易很常见，不能自动认定重复。固定 6,759 条年报行中严格字段元组也可能在同一文档内重复；文档和印刷行身份应保留，不得按字段元组去重。

## vNext 并行账本最小安全路线

1. 用 `source_id + wh-url 文档 ID + 归档 PDF SHA + parser/version` 固定每份文档观察；分片年报先验证 9 片顺序及重组字节哈希。保留现有候选行 ID 与源 URL，审计只附加旁路状态。
2. 按公开目录标签把 278e 年报和 278-T 放入不同来源层。每条观察附文档、页码和印刷行位置；原件不可定位的行进入隔离。报告完整度和正式同源关系单列为 `unknown`。
3. 对 OGE 278-T 先按**原件哈希、官方 ID 或明确修订链**建立文档关系；名称/日期/金额相似只形成待核对候选。跨年报与 PTR 的 361 条粗匹配逐条检查资产、所有人、原件行及版本关系。
4. 并行账本输出 `observed`、`candidate_possible_overlap`、`document_identity_verified`、`quarantined` 等可追溯状态；没有可核验同源关系时不自动删除、合并或覆盖现有正式候选。

固定样本还显示几个 `filed_at` 与 URL 文件名年份表面不一致的文档（例如 Miller `08.29.26` 文件名对应候选 `2025-08-29`）。文件名不是正式申报日期证据；vNext 必须从原件签名/提交字段单独核验日期。**本轮没有以文件名修正任何候选字段**。

复算命令（在本隔离工作树）：

```powershell
python backend/scripts/audit_wh_url_candidate.py --candidate .local/disclosure-candidate.json --index .local/whitehouse-index.json --candidate-commit edd5f8081c3c068dbfc1350179cd00a21d5441a7 --evidence-commit 5fe5f0adb3181c5d1552dff3f07336464c3043a7 --output .local/wh-url-audit-replay.json
```

本轮实际重放结果与固定审计 JSON 完全一致，并运行 `test_whitehouse_candidate_audit.py` 的 2 项测试。没有改正式候选、生产 refs、前端 HTML 或用户对七个收益值显示“—”的决定。
