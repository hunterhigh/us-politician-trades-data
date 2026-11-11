# 11 月 10 日线程 B：结构整理

范围仅限结构、失效入口和工作流归并，不增加功能或改动数据规则。基线为 `7188da882e2f812d67100051f0d15dd1282b6be9`；在独立工作树的 `codex/nov10-structure-only` 分支完成。`fact_policy/`、`builder.py`、发布／整合实现及 publish、oge、market、senate 工作流保持基线原样；原先位于 CLI 中的相应适配代码只作机械拆分。

## 内容一致性验收

固定 evidence `2ad3bf1aae3d50a1a20d45b4db3e275ec9bb9831`、review `f64f0e700a9e6be4f9450436b4b5d9642eab579d`、state `601c82ad662a454bd3e0341516e9e3fa2bfce957`。保存的 18,777 个输入文件（含官方原件）哈希全部不变。

原始代码单独从基线提交导出，与整理后代码各自通过正常 `build-disclosure-candidate` 和 `prepare-publication` 入口运行。两次使用同一份固定 review 中的三个来源候选、空基底、同一环境及 `generated_at=2026-11-10T00:00:00Z`；候选、截点审计和完整快照共 **5,218 个文件逐字节一致**。其中快照为 5,216 个文件，584 人、27,228 交易、19,499 持仓；输入候选含开口金额记录，为 27,238 交易、19,578 持仓，沿用原有过滤行为。

候选 SHA-256：`076b8185524e9b3ca208c89532bd7666b934ffd999c838c815663f370ecec0fd`。

本机完整证据、输入及产物留在 `.local/structure-acceptance/`，验收脚本为 `.local/accept_structure.py`。不提交证据、缓存或生成快照。本次不发布、合并或移动任何数据引用。另行尝试的 `build-disclosure-status` 原件重建曾在整理前因 House saved profile 与本机重放不同而失败；该尝试不作为通过项，也没有改写缓存或放宽校验。上述验收从已保存的来源候选开始，不声称完成全量原件重新解析。

## 结构与行数

`__main__.py` 从 1,416 行变为 6 行，只调用命令注册表入口。原有执行体按 House、Senate、OGE、market、snapshot 分到五个 CLI 适配模块；保留的 35 个执行体逐一核对 AST 相同。参数与错误回执仍由原有逻辑处理。

45 个纸表／扫描识别实现移到 [`frozen_paper/`](../backend/src/unison_snapshot/frozen_paper/README.md)，标记冻结、不再开发。迁移只调整导入和四处资源路径；除退役 Senate 生产器外，其余执行 AST 不变。旧路径保留指向同一模块对象的兼容入口，供未修改的工作流、策略及其他线程继续调用。

物理行数口径：`backend/src` 下所有 `.py`、`.js`；`backend/tests` 下所有 `.py`，包含空行、注释和兼容入口，不计 `.local`。

| 范围 | 整理前 | 整理后 | 变化 |
| --- | ---: | ---: | ---: |
| 源码 | 71,520 | 71,603 | +83 |
| 测试 | 60,967 | 60,887 | -80 |

源码净增包含 45 个兼容导入入口及拆分后的适配结构，没有通过压行减少数字。未新增测试；只迁移现有 patch 目标，并删除仅覆盖退役生产器／入口的测试，保留正式纠错数据消费的测试。

Paddle、TrOCR 在此基线已无源码、专用测试、依赖或工作流残留；仅 `docs/status.md` 保留其退役事实。本轮没有将仍在使用的 Tesseract、RapidOCR 或蓝墨日期 ONNX 读取器误删为这两个实验。

## 逐项命令结论

基线实际有 84 个顶层命令（47 个原 CLI 注册项、37 个惰性注册项），整理后 72 个。原清单中的若干名称是被工作流调用的子命令，不是独立顶层命令。

| 删除的命令入口 | 同步清理／仍有用途的共享实现 |
| --- | --- |
| `archive-house-ptr` | 删除仅供它调用的 `house.archive_indexed_ptr`；正常队列使用 `drain-house-ptr`，共享原件归档保留。 |
| `archive-senate-paper-pages` | 删除未调用的整批 `archive_review_paper_pages` 包装；正常报告采集使用的单报告页面归档保留。 |
| `assemble` | 删除独立 CLI 包装；`store.assemble` 由现有 `demo` 使用，保留。 |
| `correct-reviewed-senate-paper` | 删除旧生产器、路径包装和专用测试；正式候选仍使用的 `correction_for`、`reviewed_record`、`reviewed_counts` 及其消费测试保留。 |
| `fetch-disclosure-status` | 删除无调用的 CLI 包装；正式客户端使用的 `PublicSnapshotRepository.disclosure_status` 保留。 |
| `fetch-public` | 删除重复 CLI 包装；公开读取仍由 `client/fetch_snapshot.py` 及共享仓库客户端提供。 |
| `observe-senate-paper-slots` | 删除专用 `senate_paper_observation_cli.py`；候选和已保存纸表重放仍需的观察／投影实现保留并冻结。 |
| `parse-senate-members` | 删除离线 CLI 包装；正常采集和年报使用的名册解析函数保留。 |
| `parse-senate-search-page` | 删除离线 CLI 包装；正常分页采集使用的解析函数保留。 |
| `publish-demo` | 删除重复 CLI 包装及其专用测试；完整 `demo` 入口与共享存储实现保留。 |
| `qualify-house-paper-observation` | 删除无调用的单条 CLI 包装；正常 `bridge-house-paper-batch` 使用的资格实现保留并冻结。 |
| `rollback-demo` | 删除无调用的 CLI 包装；共享存储实现及正式回退工作流不改。 |

| 保留的名称 | 实际用途与调用入口 |
| --- | --- |
| `activate-senate-amendment-supplement` | `replay-senate-history` 的 resolve 路径间接调用，激活已绑定的修订前件补充。 |
| `archive-annual` | `whitehouse.yml` 调用 `oge-whitehouse archive-annual`，归档可直接取得的年报。 |
| `archive-house-holding-report` | `house.yml` 内嵌 Python 通过 subprocess 真实调用，归档年报原件。 |
| `build` | `senate-annual build` 及 `oge-review build` 构造既有来源候选／review 产物。 |
| `collect` | `senate.yml` 调用 `senate-annual collect`，正常年报采集。 |
| `coverage` | `whitehouse.yml` 调用 `oge-whitehouse coverage`，生成可归档的官方披露库存。 |
| `crosswalk` | `whitehouse.yml` 调用 `whitehouse-disclosures crosswalk`，保守关联 OGE 目录与白宫公开报告。 |
| `overlay` | `senate.yml` 调用 `senate-annual overlay`，将合格年报合入该来源候选。 |
| `plan-senate-amendment-backfill` | `replay-senate-history` 间接调用，建立明确选定报告的历史前件计划。 |
| `prepare` | 共享 `data-trees` action 调用 `data-trees prepare`，准备固定引用的数据工作树。 |
| `publish` | 共享 `publish-data` action 及检查点路径调用 `data-trees publish`，持久化受控来源产物。 |
| `request-plan` | `whitehouse.yml` 调用 `oge-whitehouse request-plan`，仅记录需申请的目录状态，不发起申请。 |
| `resolve-senate-amendment-backfill` | `replay-senate-history` 间接调用，验证并解析修订前件补充。 |
| `sync` | `whitehouse.yml` 调用 `whitehouse-disclosures sync`，同步公开页面和有界原件批次。 |

`operations.md` 已移除不存在的 `architecture.md` 链接和旧候选分支／首次切换描述。`house-holdings-replay.yml` 合入 `replay.yml` 的 `source=house, mode=holdings`，保留原 push 文件触发、运行容器、来源写锁、检查点循环及后续候选组装步骤；指定的四个工作流未改。

额外检查：现有 339 个模块的 2,588 个唯一测试检查全部通过，无新增测试。先保存已通过检查，再把尚未完成项分成三个互不重叠的组运行；最初缺少 `fontTools` 的三个模块在本地隔离环境补齐锁定版本 `4.62.1` 后也全部通过。35 个保留 CLI 执行体及 45 个冻结实现的 AST 核对通过。合并持仓任务的步骤、容器、超时与并发配置与原工作流完全相同，其他 replay 任务原样保留；工作流语法检查通过（本机旧版 actionlint 的临时输入仅省略其尚不认识的既有 `queue: max` 字段，实际工作流保持该字段）。详细本机检查回执：`.local/structure-verification.json`。
