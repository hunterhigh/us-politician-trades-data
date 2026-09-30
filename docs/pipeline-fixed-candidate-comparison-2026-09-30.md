# 固定 review 候选与三源 shadow bundle 对照（只读）

日期：2026-09-30。本报告承接[投影缺口审计](pipeline-projection-gap-2026-09-30.md)。最新前端基准仍为用户指定的 `C:\Users\admin\Downloads\politician-disclosures (3).html`（SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`）。没有改变前端、正式候选或生产工作流。

## 固定输入与校验

- 三源 shadow bundle：Actions run `36662639609`，manifest run ID `pipeline-shadow-36662639609-1`，代码 commit `96d86b2cf6e4dac003133d88bad061bf4f2669a1`；本地 manifest SHA-256 `6A56256D4C0823B32D254C30CF1398489607312E60E313A555B9A0323CB252CB`，candidate_rows SHA-256 `C5B893DBFC00F9EBAD1509F6F32C7AC9E1B43A563FF20655A2EA7EB0210EC348`。4 个声明输出和 13,572 行守恒均通过。
- House、Senate 正式来源候选取自固定 review commit `9749718e5ae837057db7c031d431714fc72988d1` 的 `candidates/sources/house_clerk-current.json`（Git blob SHA-1 `56ef049f6e6b489eac445178aefb74960d00e40f`）与 `candidates/sources/senate_efd-current.json`（`fa0c42a79a2c13999a5909bab3fd083cc2aaff60`）。OGE 候选取自固定 review commit `7fb8c1a6949f2444224f5080bce67b51fc8dcadb` 的 `candidates/sources/oge-current.json`（`43c1274dd25f3a7092a4e182937482f5eaf284d4`）。已通过 GitHub 固定提交树核对这三个路径及 blob ID，下载字节又由本地 Git blob 哈希验证。
- bundle 的来源记录固定 `evidence` commits：OGE `5fe5f0adb3181c5d1552dff3f07336464c3043a7`；House/Senate `80f086267677b8fde34314f24024bdc505d97cb0`。本轮没有逐份重读官方原件，也没有另行验证旧正式候选每行到该 SHA 的关系。因此价值相同只能算初筛，不能作为正式投影证明。

## 对照结果

| 来源 | qualified | 同 ID、同官方文档与 URL 直接匹配 | 同文档同观察字段值、但 ID 不同的暂定匹配 | 值重复而歧义 | 正式候选无对应文档 | 直接匹配字段差异 |
|---|---:|---:|---:|---:|---:|---:|
| House | 8 | 8 | 0 | 0 | 0 | 0 |
| Senate eFD | 12 | 12 | 0 | 0 | 0 | 0 |
| OGE | 5,040 | 0 | 4,101 | 854 | 85 | 不适用 |

对照字段：`asset_name`、`transaction_date`、`transaction_type`、`amount_low`、`amount_high`，及 ledger 选定且非空的 `owner`、`ticker`。同 ID 的 House/Senate 行进一步逐项比较全部双方均有的选定观察字段，没有发现差异。OGE 的 4,101 只是同一文档里恰有一个同值正式事实；旧正式候选没有可核对的 shadow 行定位，所以**没有建立 4,101 条正式身份绑定**。854 条遇到同值多笔，不能随机选择。85 条所在的四个官方文档在固定正式候选中完全没有交易：`2c347e1e940d65a885258cfd002c0504`（69 行）、`d3bcc865ef5e39f585258718002e4aa5`（8 行）、`4b988978d7a864f785258de4002dbf44`（7 行）、`86c03b37455893e18525860f0027ddf7`（1 行）。是否因修订、重复或资格规则导致，尚未裁决。

此前发现的 OGE 跨文档同 `candidate_id` 八对仍完整保留在审计输出中：两个官方文档 `1a3bd00f7ac33a6385258718002e4aa3` 与 `d3bcc865ef5e39f585258718002e4aa5` 共享 PDF SHA，但八个行 ID 分别重复。后者正好属于固定正式候选未收录的文档；这不证明它应被补入或永久排除。必须由独立的修订/重复关系规则判定。

## 实现边界与下一步

`backend/src/unison_snapshot/pipeline_qa/canonical_compare.py` 仅接受本地固定 bundle 与显式给定的候选文件、预期 Git blob SHA-1、review commit。哈希、来源、申报 ID、官方 URL 和字段不符就拒绝或列出；没有模糊名称匹配、默认人物、默认 filing、自动补 ticker、正式交易创建或 ref 写入。对照结果固定 `projection_ready=false`。

下一步必须针对 OGE 取得固定 review 抽取/资格产物中行定位与正式交易 ID 的可验证关系，解决 854 条重复值和 85 条无正式文档，并将 House/Senate 的已匹配行回连固定原件 SHA、人物与申报、修订链。完成全量旧/新逐行差异、最新 HTML 验收与回退演练前，不得把本报告的数值匹配当作切流许可。
