# 白宫 278e 年报固定候选逐行重放

本审计仅用于现有合格数据的无损迁移核对，不更改 `review`、`evidence`、`main` 或候选。最新前端实现仍以用户指定的 `politician-disclosures (3).html` 为准；其本轮核对哈希为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。七个早于可验证行情首日的收益值继续视为不可计算，页面显示“—”；本年报审计不计算收益。

固定输入：`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` 的 `candidates/disclosure-current.json` 与白宫 `whitehouse-278e-hybrid-geometry/v8` 抽取；`evidence=5fe5f0adb3181c5d1552dff3f07336464c3043a7` 的归档元数据和九个分片路径。文档 ID 为 `wh-url:0c14d3849ca60768024e470b`，原件记录 SHA-256 为 `1cc7951c6f72fab008e921903c9a1d03d41a9910239f954e208b501d608553a3`。抽取字节 SHA-256 为 `f44d6c7de59f16111a39a88d7993969e7ba609d08da8201fa36c605268d29547`，OCR 检查点固定树为 `c57ecdcd0e780fa7dc256fae6d0fe728735c8f09`。代码检查候选和抽取 Git blob、归档元数据 blob、九个分片路径及检查点树身份，不重新 OCR。

| 固定抽取处置 | 行数 |
| --- | ---: |
| 持仓 | 3,999 |
| 交易 | 6,759 |
| 明确排除 | 21 |
| 隔离 | 17,320 |
| 合计 | 28,099 |

第 7 部分另有 14,526 条隔离，交易及隔离的 21,285 个物理 locator 均唯一。审计对每条旧候选以 `PDF SHA + part7 + 物理 locator` 重建稳定 ID，比较资产名、所有人、方向、交易日期、金额上下限、申报文档、人物、来源 URL、申报日期、来源类别和验证状态。**6,759 / 6,759 条 ID 与上述字段一致，冲突 0，额外候选 0**；排序后绑定摘要 SHA-256 为 `5cd01411f138f17696d471f3e62aeecefc773a4fd225127569fd2b3d6104d153`。旧候选的 ticker 是后续增强字段，不能拿它冒充原件提供的证券标识；本轮不修改 ticker、持仓、人物或旧 ID。

固定候选中 361 条年报交易与某白宫 278-T 仅共享人物、日期、方向、金额区间。资产及原件行尚未证明同一事实，不能合并或删除。旧抽取的 `source_row_census_complete=false`，所以 28,099 是该固定 OCR 抽取的行守恒，并非 927 页印刷交易的独立全量证明。归档元数据及九个分片在固定 `evidence` 树中的路径已核对；**本轮未重新读取并校验仓库中九个原件分片的字节**，不能把路径核对称为原件字节重验。旧报告交易的 `owner=Unknown` 继续保留。

复算：

```powershell
python backend/scripts/audit_whitehouse_annual_fixed_replay.py --output .local/whitehouse-annual-fixed-replay.json
```

本轮实际运行上述脚本，结果见 [`whitehouse_annual_fixed_replay.json`](../backend/tests/fixtures/whitehouse_annual_fixed_replay.json)；本模块 `unittest` 2 项通过。该结果是迁移桥的已验证覆盖，不代表 PTR 重合已闭合，也不授权生产切流。
