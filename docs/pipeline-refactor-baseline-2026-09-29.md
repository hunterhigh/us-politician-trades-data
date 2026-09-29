# 数据流水线重构基线冻结

冻结日期：2026-09-29
集成基线：远端 `origin/code` 固定提交 `0d33e949a503770767d35fa64bff6709aaa395b1`
集成分支：`codex/pipeline-refactor`，基于上述代码提交的独立托管工作树

## 生产分支固定提交

| 分支 | SHA |
|---|---|
| `code` | `0d33e949a503770767d35fa64bff6709aaa395b1` |
| `main` | `958b173ca37964dfa60e4762c6ef2931e3ed06ea` |
| `market` | `29251969fbadb06565781aa9c9d5bafd3ce6e00e` |
| `state` | `e36587050c1493a130c0e1adf5198c3162046fe9` |
| `evidence` | `80f086267677b8fde34314f24024bdc505d97cb0` |
| `review` | `9749718e5ae837057db7c031d431714fc72988d1` |

远端正式 `main` manifest 的 `snapshot_id` 为 `19744d00fd3fa15249697ce3de7f21b29d1b6dc97cafdfa3a973b5194c127c74`，`data_cutoff_at` 为 `2026-09-27T23:59:59Z`，固定引用上表 `market` SHA。以上是冻结时读到的远端 refs，不保证后续生产更新仍保持这些值；任何对照或回归需再次固定实际输入 SHA。

## 前端及交接实现哈希

用户指定的最新 HTML：`C:\Users\admin\Downloads\politician-disclosures (3).html`，大小 `68,817,061` bytes，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。其内嵌快照声明 `generated_at=2026-09-29T02:18:01Z`、`data_cutoff_at=2026-09-27T23:59:59Z`，与冻结的生产快照 ID 和 market SHA 相符。

`review-input/` 仅作为旧实现对照，文件均保持不变：

| 文件 | SHA-256 |
|---|---|
| `scripts/process_snapshot.py` | `A2CD37CBF447F591BF11F7F397F0FCB817BA1032F6CA5C9CA0EFB967F50C7A4B` |
| `scripts/query_snapshot.py` | `4780DAD964F27BB0E5B892DC22EB13C25A61D60BA1F1B1E7D7C60D33FD8FB80A` |
| `scripts/render_dashboard.py` | `AE3FC8A38CDC1DF7C7DDFF3B9F3F834401763D09D5B9891E56022B786F3F6C0C` |
| `tests/test_process_snapshot.py` | `8D19207AF067C175448DA6FE6CCF37B03E9E30F358F64612945A026C77DE971A` |
| `unison-data-contract.html` | `D8CB58C9AC38FCDB69A12DB090A2ABB9EB20B17492B48FBAC264C1661E8B68F8` |

## 工作流定义冻结

以下 YAML 内容由集成基线 `0d33e949` 固定；SHA-256 用于确认文件未被静默更换。

| 工作流 | SHA-256 |
|---|---|
| `house-state.yml` | `A94E4F21699A73D980F7975527A118E29B4A8E2738F2EF6DB63A62A1D97D95B2` |
| `house-review.yml` | `53281A64781091AD95462882BB6C6A298FC765DD8E82990ECB018BFD03195A0A` |
| `senate-efd.yml` | `D3F32DFCF2EDD6BFB230B734186BBCCDFECDDBCBE3D1E65646CAA53D314FF021` |
| `senate-roster.yml` | `8A2DBCD9ABCA7595CA8A2A21616F884EB2CC0B974A12B9F625233CF65882D91E` |
| `senate-paper-pages.yml` | `16645A7A6CA90771B4EE04388B1C61C8D286599BC0B616150899E50552E9156C` |
| `senate-amendment-history-plan.yml` | `A24C75FB4E84F890A8FD3DCDD60408752978E69C7AB9E74E097B9A04501FD62C` |
| `senate-amendment-history-resolve.yml` | `BDEB1D85E66821F1468C40741B3FD6FB9B657AD8C934F5BBE3B94EB583E2C53A` |
| `house-holdings.yml` | `065BB60BE71CA272E24A8ACAFCC7776A7D8AEF2AEF7B9D2C5444A15511307755` |
| `oge.yml` | `696FD9CD6008069E7D78760C9AA637F8980642D57869E64EA31117409D180CE2` |
| `publish-complete.yml` | `DE774ECC0F1065AE0DDA10A5ECD621F1FEA08C39487670498AC3BA0026FB30A9` |
| `publish.yml` | `805DC17E3EBB30E26FA0EC261F968B4E1BE3460578ED218CB0E22059C774A2BD` |
| `rollback.yml` | `D885FBB9DD04CC5EF6A086EB69F633BBE452136BF4829391A8BB446DD9EF49C2` |
| `ci.yml` | `92B08A73912327E64B368295FE1C15A74B22AD0AABBA012BE7C198F709119C9E` |

## 原工作区未提交改动

原用户工作区 `C:\Users\admin\Desktop\agents\whitehouse` 在开工时基于本地 `code` 提交 `1e07ed224222f30b43eff4794a0386b2406a8285`，该提交与最新远端 `code` 已分叉。其未提交工作和附件均完整保留在原路径，没有覆盖或批量复制到集成工作树；owner 无法从 Git 状态或会议记录确认，故不猜测。

- House PTR 与测试改动可作 D 包的候选输入，但其中移除通用 OCR 置信度门槛、允许缺失通知日期及部分 filing status 自动晋级的行为，须对固定官方样本和最新 HTML 消费语义单独验证后才可移植。
- Senate paper OCR 改动可作 F 包候选输入；移植前须以固定的 9 份/52 页归档材料检查行数守恒、锚点去重及运行成本。
- `release_readiness.py` 的原工作区版本移除了最新远端已有的 OGE 数量一致性门禁；不得整文件移植。Senate failure 阻断行为单独评估，并保留 OGE 检查。
- README、目标、状态、总体设计、生产配置等旧分支整文件比远端生产状态陈旧；只可在核实事实后择取仍有效的文字。HTML 基准优先级作为用户明确的新决定独立记录。
- 用户提供的 ZIP、其两份文档副本和未提交会议记录均保持原样，作为输入材料，不覆盖集成基线。

本文件只固定起点与待移植决策，不代表任何解析补丁已验收，也不代表本轮已运行测试或生产操作。
