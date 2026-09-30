# House PTR 403 份固定原件全量离线 shadow 账本

运行日期：2026-09-30。最高优先级前端 HTML `politician-disclosures (3).html` 仍存在，SHA-256 核对为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。本账本是来源审计产物，不是前端五数组或正式候选发布。

## 固定输入与执行代码

| 项目 | 完整提交或哈希 |
|---|---|
| 运行代码（干净 managed worktree HEAD） | `58eb265e35b291bbe72111501ee84ee65ab8d892` |
| 官方 House 原件 `evidence` | `0bfde57073838652d58ed1fa814b42c49bf7bd5c` |
| 存档抽取、资格、失败及候选 `review` | `edd5f8081c3c068dbfc1350179cd00a21d5441a7` |
| run ID | `house-ptr-403-20260930-58eb-0bfd-edd5` |
| [完整 run manifest](house-ptr-403-shadow-manifest-2026-09-30.json) SHA-256 | `0e97d62d0f64fcc3da4a467f7f753ff69a38d382e3c3d3d5c0d9e090c8b5abce` |
| `candidate_rows.json` SHA-256 | `56a49d940234c7c9d41c259b8c993a785f74ad52a9179235b3f356635ed2cebe` |

本机的完整 run 目录是 `C:\Users\admin\AppData\Local\Temp\house-shadow-full-20260930-58eb-0bfd-edd5`，包含 `manifest.json` 与约 15.7 MB 的 `candidate_rows.json`。本审计分支另保存了 manifest、[403 份逐文档账本](house-ptr-403-shadow-document-ledger-2026-09-30.csv)和[候选对照结果](house-ptr-403-shadow-candidate-compare-2026-09-30.json)；大行数组未混入代码提交。完整目录可直接作为 `pipeline_qa.bundle_cli --run-dir` 的 House 输入，须搭配相同 `code_commit` 的其他来源 run。

在固定 `code` 的独立干净 worktree 上，调用**原有** `unison_snapshot.house_shadow` CLI，逐份传入 403 个从固定 `evidence` 树列出的文档 ID。该适配器重新校验每份 PDF 的 SHA-256、元数据、抽取/资格或失败记录的同源性，将每条存档抽取行恰好映射为合格或隔离候选观察。它没有重新 OCR 或重跑 parser，也不将漏读的物理行凭空补成候选。运行全程未写 `evidence`、`review`、`main`、`code` 或工作流；输出只写本机新建目录。Git 仓库少 73 个 House blob，先从此前 GitHub 只读缓存逐个复验 Git blob SHA-1，放入**独立临时 Git object store**；对 1,591 个 House 输入 blob 的预检为零缺失，未写共享 Git 对象库。

固定新旧 House 子树逐树哈希一致：`evidence` 的 `house_clerk/documents/2026` 与此前 `5fe5f0adb3181c5d1552dff3f07336464c3043a7` 相同；`review` 的 `house_clerk` 与此前 `a814fde2bb9664c2b97981a32ce86ab9b3daf223` 相同。远端 `code` 已变化，因此仍以本次完整 SHA 记录执行代码，不拿旧实验分支 SHA 冒充同版运行。

## 全量终态与行守恒

| 固定原件 | 终态 | 已观察抽取行 | 合格行 | 隔离行 |
|---:|---:|---:|---:|---:|
| 403 | `parsed` 378；`failed` 21；明确 `no_rows` 4 | 4,868 | 3,352 | 1,516 |

每份文档恰有一个终态；`no_rows` 的 4 份均有原件明确无交易声明。21 份 `failed` 在 manifest 中为 `row_count_status=unknown_parse_failed`，逐文档 CSV 的已观察行数留空，**不是零物理交易**。全量 4,868 条存档抽取行恰等于 3,352 条合格加 1,516 条隔离；排除与未识别均为 0。`house_shadow` 的 `validate_run_manifest` 在写出前通过，输出的 candidate_rows SHA-256 与 manifest 声明一致。

该守恒仅覆盖**已被存档解析器发现**的行。`parsed` 仍包括经图像审计确认未完整覆盖的 `9116290`（原件 11 可见行，存档仅 4 行，其中 1 行已正式晋级）和 `9115813`（至少 9 可见行，存档仅 1 条畸形隔离行）。因此 378 份 `parsed` 不等于 378 份物理行完整；账本不宣称 House 全部交易已发现，也未修改这两份的存档资格或撤下现有候选。`9116260`、`9116326` 仍在固定 review 的失败集合，行数维持 unknown。详见[报告覆盖语义](house-ptr-partial-report-semantics-2026-09-30.md)。

## 固定候选对照

新 `review` 的 `candidates/sources/house_clerk-current.json` Git blob SHA-1 为 `ccea4afd5363b29a1858475a7d920dc34fe625ee`，从 GitHub 固定 blob API 只读获取并复验内容哈希。其 3,352 条 PTR 候选 ID 与 shadow 的 3,352 条 `qualified` 行 **完全一致**：移除 0、新增 0。对相同 ID，必需投影字段（资产名、交易日期、方向、金额上下界）及来源文件身份差异均为 0；各资格文件中交易完整字典与现存 House 来源候选完整字典的差异也为 0。这是固定存档结果的对账，不是本机新 parser 重新资格的结果。候选对照与 manifest 哈希见 JSON 附件。

## 远端工作流与三源 bundle 的缺口

现有 `.github/workflows/house-shadow.yml` 是手动、`contents: read` 的只读入口，但每次只接受 1–25 个手输 `document_ids`，固定 403 份至少需要 17 次运行；其默认输入仍是旧 evidence/review 提交。现有 `.github/workflows/pipeline-shadow-bundle.yml` 只接受**一个** House `run_id`/attempt，下载**一个** House artifact 并作为单个 `--run-dir` 输入，不能直接收齐 17 个分批 House run。`bundle_cli` 的库函数可以验证多个来源目录，但当前远端 workflow 没有传入多份 House artifact 的接口。

本次完整 House 目录可以在本机与同一个 `code=58eb265e...` 的 Senate/OGE run 合并；它不是 GitHub Actions artifact，因此不能直接填进当前远端 `house_run_id` 输入。要远端全量执行，需在独立手动 House workflow 中增加“固定提交下列举全部 403 份”的受控选项、输出单个完整 House artifact，验证 403 份与 SHA 集合后再供三源 bundle 下载；或扩展 bundle workflow 接受并汇总固定分批 artifact，同时核对无缺失/重复和同版代码。两种方式都必须继续限制写权限、固定代码与证据/审查提交、保留失败文档的 unknown 行数。本轮没有修改共享 workflow 或触发生产重试。

本轮实测为原适配器的 403 份完整 CLI 运行成功、1,591 个输入 blob 无缺失、403 份状态及 4,868 行守恒、3,352 条候选 ID 与全字段精确对照。没有把交接材料中的测试数当作本轮运行结果。
