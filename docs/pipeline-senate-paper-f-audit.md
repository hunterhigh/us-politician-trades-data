# Senate 纸面 PTR 离线审计（F 阶段）

审计日期：2026-09-29

状态：归档输入哈希闭合；OCR 未运行；没有生成逐行提取或候选数据。

工作分支：`codex/pipeline-senate-paper`，基线 `origin/code`=`0d33e949a503770767d35fa64bff6709aaa395b1`，本地阶段基线提交 `59e4401968f83b6184c20060c88769d12fd901f9`（包含共享 evidence ledger v1）。

只读证据引用：`origin/evidence`=`80f086267677b8fde34314f24024bdc505d97cb0`，`origin/review`=`9749718e5ae837057db7c031d431714fc72988d1`。

## 范围和输入

只检查已有 Senate 纸面报告 viewer HTML、已归档页图、页元数据、manifest 与 discovery 快照。哈希闭合校验时设置 `GIT_NO_LAZY_FETCH=1`，只访问本地已有 Git objects；不做来源网络访问、不写 evidence/review、不运行工作流。开始盘点时曾直接读取缺失的 review status blob，Git 触发一次 lazy-fetch 并因网络连接失败；未取回任何内容。发现该行为后停止该类读取，后续全部校验均禁用 lazy-fetch。

本地 Git 树列出 9 份 paper PTR。两份可读取的 discovery 快照都列有相同 9 个 paper PTR ID。`origin/evidence` 中有 9 个页面 manifest、52 个 GIF 页图和 52 个页元数据文件，共 113 个页面归档文件。每份页面数都与该报告 GIF 数量一致。原件入口在 `senate_efd/reports/<document_id>/<entrypoint_source_sha256>.html`；本批不是 PDF 文件集，解析所需页图是已归档 GIF。

离线校验结果：

- 9/9 个 manifest 的 `manifest_sha256` 按项目生成算法复算一致。
- 9/9 个 viewer HTML 内容 SHA-256 与对应 manifest `entrypoint_source_sha256` 一致。
- 52/52 个 GIF 页图内容 SHA-256 与各自 page metadata 一致；metadata 的 `document_id`、页号和总页数与归档路径及 manifest 相符。
- 缺失 blob：纸面页 manifest、GIF 和 page metadata 为 0。`origin/review:status/senate_efd.json` 对应 blob `5d3dcf9053244633621bfdf74fa4d5af9782718a` 不在本地对象库。最初读取该对象时 Git 曾尝试 lazy-fetch 但失败；之后没有再次尝试。因此这里不能声称核对了 status 当前所指 discovery/catalog，只能确认两个本地 discovery 快照包含相同的 9 个 paper PTR ID。无其他网络读取成功。

| Document ID | 页数 | Viewer HTML SHA-256 | manifest SHA-256 |
|---|---:|---|---|
| `068274e1-4b7a-4453-a242-563dde4c10d8` | 5 | `107c79b1276ae99ef9321547420e2303790fcc3cb110f43bf64e4c3ddb34bb6c` | `764c5e16ae4c43fe5b62f0d27bd15095d0ee7fd448dc1e75389ed50c2172a555` |
| `3a4c5095-028a-4614-a692-836719da4e63` | 5 | `332bf8d6b8398c55443935a091e3b3a40617ffd6148a377c6a20c4b6902d83a3` | `c283c01d61f0fde498c208c138f6056b18f09d00383bd64d667e32abb8827609` |
| `929216d5-5dbd-429c-858c-1e9332924627` | 9 | `f36d9a68aca0fe198fc8b5d82d073a9003cf6f445b94e0fc57d11019efd1636a` | `f18213ffaf8d6ff02092502e913cf751e6a5b86d37b0f200b03580646249f5b0` |
| `a0d25e8f-fe54-4328-a7ea-504da008742b` | 7 | `932f61763b40721319378f36dd465c1e260dbeed1a1cff3e24eee1df42b4d8f8` | `a876a7d772f5e0ce5d4fa2f0c17a97b539f60ee5eee3dead025e23b465dd227c` |
| `d02263c3-381d-4ee9-8d84-2c44d9baa59e` | 5 | `7b505e3286ed0a1e4ca77d3502d790a471e19ebe3a466346f1d95219aab6f1e2` | `450a22ce0b5074cb1c3c40e45042e96e6a457a63dd3f4ecc40772a0a44e1ec68` |
| `d337c392-e0aa-428e-be93-44a327b90d08` | 6 | `d4ac1e161473fbab615cb338978529ae5220c96f165f8e9e5ac351fb62a8a94f` | `97a3da8e1a8f20fd35533c136b4dba568fbc27c20f740fa89d0304021409024b` |
| `ec20cd93-6702-4a29-b3a6-983f4b17f365` | 4 | `82f0250495dd9391e948e228b36a0cf7dd619a84456762a64ec16d1e550bb76f` | `771f56fccd5ad955693b8ca5f046af788c67da17224d5b70921a742c095b715c` |
| `f028d2ce-4ab7-41a8-a67a-91675b6941d7` | 6 | `7f3c7f47da7e9a504c20dcac879e4665b077989a0fbcc0ad71182c99efa0d721` | `2cfc40a3198d792c8298ec5d418fe0b5cbfbde095ec204aceae22132cda0a318` |
| `f873aeb4-adbb-4934-a188-79416a2e4c76` | 5 | `84d032efe572500d52d138312df577d5b4a0a869c43f3497524fae331654d742` | `d9fb0d4af0d31b4cab09ad1e7394c50a1f654f415337a15605a6d349cece8a63` |
| **合计** | **52** |  |  |

## OCR 与解析限制

本机 Python 为 3.11.9；`pdftoppm` 为 26.07.0，但本批输入是 GIF，且当前解析器直接把已归档 GIF 交给 Tesseract。未找到 `tesseract.exe`（PATH 和常见安装路径均无）。因此无法复核原始页中日期、勾选、金额格或文字的 OCR 观察。

现有解析器若运行，会调用 `tesseract <gif> stdout -l eng tsv`，未传 `--psm`、语言包版本或额外 OCR 配置。实际 Tesseract 版本和有效默认参数未知。没有执行这些调用，没有伪造 OCR 字词、置信度、页行/单元格观察或候选资格结果。本次没有改动任何 review 状态；由于 status blob 缺失，不能声称已核验 9 份报告的当前 review 处置。

缺少 OCR 引擎属于可补齐的本地执行条件。本次未安装新依赖；继续 F 阶段前，应由任务环境提供可核实版本的 Tesseract 与 `eng` 数据文件，然后对同一组已哈希闭合页图离线回放，保存准确命令、版本、语言数据和完整行级处置账本。不要为填补输入缺口重新采集来源。

## 按指定 HTML 的字段映射边界

前端基准文件 `C:\Users\admin\Downloads\politician-disclosures (3).html` 在审计时大小为 68,817,061 bytes，SHA-256=`D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`，与用户指定值一致。HTML 的 canonical 消费集合含 `transactions`；当前 HTML 直接依赖交易日期和申报日期作为不同时间口径，交易金额上下界、交易类型、资产名、所有人、来源/来源 ID/来源 URL 与验证状态；ticker 可空。字段映射应以该 HTML 的实际字段语义为准，旧交接说明冲突时不覆盖此基准。

| 纸面单元格 / 来源字段 | 计划中的 observation 字段 | candidate 投影字段 | HTML 语义及限制 |
|---|---|---|---|
| 交易日期格 | `transaction_date`，同时保留原始字串、页号、OCR 框及置信度 | `transactions[].transaction_date` | 交易时钟；不能用报告申报日替代。日期无法唯一识别或格式/置信度不足时隔离。 |
| 报告目录申报日期 | 来源级 observation，关联 document ID | `transactions[].filed_at` | 申报时钟，与 `transaction_date` 分开保存；不能伪造时分秒。 |
| purchase / sale / exchange 勾选格 | `transaction_type`，保存局部页图、格坐标及 OCR/mark 证据 | `transactions[].transaction_type` | 只接受唯一且有局部证据支持的类型；零选、多选或不确定都隔离。 |
| 金额区间勾选格 | `amount_raw` 和选中区间的证据引用 | `transactions[].amount_low`、`transactions[].amount_high` | 前端分别展示上下限。只有选中格能无歧义映射成两端数值才可候选；未知不能改成 0。 |
| 资产描述 | `asset_name_raw`，保存词框/置信度及页证据 | `transactions[].asset_name` | 页面详情使用资产名。OCR 缺字或行归属不清时隔离，不能从公司目录补写来源原文。 |
| 明示所有人标记（S/J/DC 等） | 原始标记与规范值分别观察，留页内证据 | `transactions[].owner` | 规范值须按原件标记明确映射；没有标记或无法确认归属时隔离。 |
| 描述中明确写出的 ticker（若存在） | `ticker_raw` 与对应原文/坐标 | `transactions[].ticker`、可选 `ticker_mapping_basis` | HTML 允许 ticker 缺失。没有明确原文证据时保持 null，不从名称猜 ticker。 |
| 文件身份与归档证据 | source/document SHA、URL、页码、规则版本、parser 版本、OCR 布局/运行参数 | `id`、`filing_id`、`source`、`source_id`、`source_url`、`verification_status` | 候选还须建立可靠 `person_id`，保留官方来源绑定，并满足 HTML 对字段及缺失值的语义。仅有 `filer_name` 或行解析成功不够资格晋级。 |

此处只是目标映射，没有生成 observation 或 candidate 行。当前 `senate_paper.py` 的旧抽取结果不足以替代上述含页面局部证据的 ledger observation；在可复现 OCR 和逐行守恒、异常隔离完成前，不能声称 ledger candidate 映射、实际资格率或字段完整性已经验收。

## 验收记录

- 已做：本地Git树清单与 blob 可读性盘点；9份 manifest、9份 viewer HTML、52份 GIF 与对应元数据的离线 SHA-256 校验；前端 HTML 基准 SHA-256 校验。
- 未做：OCR、真实页面逐行解析、矩形/勾选的人工对照、金额单元格复核、candidate/diff 守恒、生产 workflow 或发布。
- 代码修改：无 Senate paper parser、OCR 规则、fixture、review-input 或 workflow 改动。本分支仅包含此前共享 evidence ledger v1 基线提交及本审计文档。
- 实际测试命令：在 `backend/` 下设置 `PYTHONPATH=src`，运行 `python -m unittest discover -s tests -p 'test_senate_paper.py' -v`；3项通过。它只检验既有 parser 的合成 word-page 行为，不代表这9份原件的 OCR、资格结果或真实通过率。
