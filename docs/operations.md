# 运行与恢复

当前生效结果、待完成项及授权见 [status.md](status.md)，事实与消费边界见 [data-contract.md](data-contract.md)。集成源码在 `code`，生产数据在 `main`；PR293 已完成源码切换。开发分支和本机工作树不代表生产版本。修改工作流、PR通过与实际部署分别表述。

## 入口与环境

公开生产数据从[main manifest](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/main/manifest.json)读取。[独立健康状态](https://github.com/hunterhigh/us-politician-trades-data/blob/health/status/production_health.json)保存采集、候选、发布与冒烟结果。客户端用法见`client/README.md`。

House原件解析和资格判定在来源处理阶段执行；候选构建、处理状态及正式发布复用已保存的资格产物，移除常规报告的逐份重复重算。修订关联仍使用现有修订流程。

House队列、OCR规划及计数用`house-review`；OGE不可变产物暂存、输入选择及白宫合并计数用`oge-review`。Senate名册更新保留采集成功水位和失败诊断；名册成功不能表示eFD恢复。发布探针及保存输入重放校验也使用正式命令。

生产命令统一为`python -m unison_snapshot <命令>`，每个命令提供`--help`。例如：

```text
python -m unison_snapshot whitehouse-extract --help
python -m unison_snapshot build-disclosure-status --help
python -m unison_snapshot build-cached-candidate --help
```

生产解析使用runtime/image.lock.json固定摘要的容器；镜像包含精确Python/Node/Tesseract、语言数据和backend/requirements.lock的全部哈希依赖。Actions按提交固定。普通作业只校验环境并安装当前源码，不临时解析依赖；镜像重建通过CI显式触发，构建后真实OCR校验。浏览器按锁定Playwright版本安装匹配Chromium。

```text
python -m pip install -e "backend[house-ingest,native-source-proof]"
python -m unison_snapshot demo
```

demo仅写`.local/demo`。修改后检查受影响路径，共享接口整合或发布节点执行完整检查：在backend目录运行`python scripts/verify.py`；浏览器检查在frontend-e2e目录运行`npm ci`、`npm test`，需要Node 24与系统Chromium/Chrome/Edge。冻结案例凭据保存在本机`.local/`与PR验证说明中，不把缓存、依赖或密钥提交到code。

## 授权与配置

Secrets只放在GitHub `production` environment：`ALPACA_API_KEY_ID`、`ALPACA_API_SECRET_KEY`、`TWELVE_DATA_API_KEY`及可选`CONGRESS_GOV_API_KEY`。不读取或记录真实值，不放入参数、URL、日志、Issue、HTML或Git。普通PR不取得生产Secrets。

OGE采集必须同时满足`OGE_COLLECTION_ENABLED`及`OGE_TERMS_ACKNOWLEDGED`；Senate日常PTR采用维护者浏览器下载；旧自动采集门禁仅用于显式历史维护。变量必须在作业取得production环境后判断。行情还需`MARKET_DISTRIBUTION_AUTHORIZED`及对应供应商分发授权。Form 201需申请的原件仅记`request_required`，不自动申请。

不新增付费资源、供应商或受限来源。现有公开GitHub部署、House公开采集和官方归档已获授权；2025 House PTR回填已从code启动，只处理固定2025年PTR，不扩年报或其他年份。production 环境按受控工作流的 `code` 分支边界运行。来源门禁遵循[数据契约](data-contract.md)：Senate单独受阻可限期保旧发布。Senate日常不自动访问eFD；维护者下载后上传，自动导入、组装并发布。

## 运行规则

下表为已采用的v1规则；尚待接入的自动化语义以status列出的剩余项为准。

| 情形 | 动作 | 发布与告警语义 |
| --- | --- | --- |
| 网络超时、连接或暂时性HTTP失败 | 最多3次指数退避，保留检查点与已归档原件 | 不把尝试时间当新水位；不触发内容回退 |
| House/OGE水位超过48小时 | 保留此前正式数据及真实水位 | 全站停发；未过期的单次采集失败不阻断其他有效变化 |
| Senate单独受阻或过期 | 保留旧事实和真实水位，公开显示stale及统计缺口 | 宽限默认30天（`SENATE_MAX_STALE_DAYS`）；20天提醒一次，超过宽限停发；来源告警保持 |
| 单文档无法解析、必要字段缺失 | 报告/行隔离，独立文档继续 | 进入状态和known_gaps，不称已全部处理 |
| 单条原件或修正无法解析 | 隔离受影响记录，继续其他记录 | 不新增全站发布门槛 |
| 旧数据变化或违反合同 | 当前解析结果进入公开变更清单；合同不符则保留现有快照 | 仅合同不符、任一来源记录数一次减少超过5%、来源过期时停发 |
| 隔离率上升或新unknown_layout | 保存分子、分母、时间窗与布局标识 | 周汇总；比上周增加超过5个百分点或新布局时单独告警 |
| 发布后正式客户端/渲染内容失败 | 绑定当前head、失败快照和已验证的last_good | 仅在内容失败时自动请求受控恢复；下一次发布按当次数据合同和正常读取结果判断 |

隔离指标分别保存整报隔离数/已评估报告数、隔离行数/已知合格及隔离行数；缺少唯一源行计数的报告不猜分母，单列未知数。按相同范围与上周最新观察比较，范围变化重建比较基线；每周复用一个汇总Issue，新unknown_layout或增加超过5个百分点单独告警。历史只留四周机器观察，旧文件不算本周新观察。

OGE重复观察更新成功采集时间，内容寻址原件的首次归档时间不变。记录使用当前解析和资格结果；无法处理的报告／行保持隔离，不要求对旧字段变化逐项证明。每次发布在main的`status/<sha256>.json`保存变更清单，由manifest的`publication_changes`引用；列出新增、修改、删除ID及修改字段前后值。按`source_id`合计交易与持仓，任一来源一次减少超过5%时停止发布。

采集成功时间、原件归档时间、解析完成、合格候选截止时间和正式发布时间分开记录。任一新尝试、失败重试、行情更新或被取消的作业均不能冒充披露更新。来源各用真实合格截止，不再按最小值裁剪；顶层日期仅作统计参考。House/OGE两项水位须在48小时内，Senate按可配置宽限检查。Senate人工文件成功导入并形成合格候选后推进水位，按正常新鲜度规则清除stale。停发时只完成评估，publish作业跳过，不刷新最后成功发布时间。

告警采用稳定条件键，已有Issue复用，条件变化才更新；来源恢复且完整观察确认后关闭对应来源告警。明确内容边界失败会生成不含原始值的安全回执，并立即复用Integrity Issue；机器人告警供排查使用，不作为额外发布门槛。修复后，同一受控流程完整成功且相同对象的校验通过，才自动解除对应Integrity条件；其他对象、其他流程或失败作业不能清除。正式发布只执行数据合同、来源记录数减少超过5%、来源过期三项停发规则。健康观察使用独立health分支与锁，不被生产发布队列阻塞。发布时效按计划工作日窗口判断，周末不制造“漏发布”告警；开发工作连续推进，不等待工作日。

House归档应按时间预算推进积压并保存检查点，不能永久停在每批10份；工作流合并及预算处理的落地情况见status。缓存可复用时不重新OCR。来源、版式或输出断言变动才重新判断资格，不能以反复验收替代核心改动。

工作流入口为house、senate、oge、whitehouse、replay、market、publish、ci、health、rollback。House 年报持仓重放统一使用 `replay` 的 `source=house, mode=holdings`，并保留原解析文件变化时的 `code` push 触发；不再使用独立的 house-holdings-replay 工作流。market准备价格交接或执行许可内审计；publish重新冻结来源并零行情请求地组装，最终原子移动main与market。市场审计或单独账号查询不触发正式发布；来源更新可重新评估停发条件。

## 发布与演练

1. 保存本机成果和固定候选，冻结code、evidence、review、前一main/market引用。
2. 用正常命令构建候选，检查自动资格、行守恒、旧事实差异和市场覆盖。所有旧记录变化自动写入公开变更清单。
3. 准备不可变预发布提交，通过公开客户端四模式、最高HTML与正常渲染浏览器检查；核对known_gaps。
4. 演练在最后发布步骤前受控失败，确认正式引用未移动；再演练绑定head及已验证前版的恢复，不触发正式切换。
5. 用户已于2026-10-29确认code切换，PR293已合入；以后正式数据发布仅按数据合同、5%来源记录数减少及来源过期规则停发。正式引用移动后执行读取与渲染冒烟。

恢复目标必须是已有`published/<commit>`标记、并有成功冒烟记录的发布前版。恢复提交以当前main为父提交、复制目标树，校验观察head并原子推送；不强推旧历史，不用网络失败授权回退。市场与披露的匹配引用、期望head或目标tag变化时拒绝恢复。

## 限时与历史

执行节奏、停止规则及待决事项统一以[status.md](status.md)的最新决定为准。本轮按授权连续推进；普通小提交、测试或PR完成不自动结束整个交付。

原35项及旧技术研究材料通过[archive/pre-v1-docs](https://github.com/hunterhigh/us-politician-trades-data/tree/archive/pre-v1-docs/docs)查阅；不再复制到当前文档。初始候选`v1/candidate-20261028`固定不动，后续修正留在开发提交与PR。原本机成果还有`archive/pre-v1`与保存分支，以及`C:/Users/admin/.codex/backups/whitehouse-pre-v1-20261028`的恢复包；官方原件只从已校验的证据引用恢复。

## Senate 维护者每周操作

不需要改代码或运行命令。每周一 01:00 UTC 自动创建或更新唯一打开的「Senate 本周待下载」Issue，其中列出已导入的最新申报日期、Senate 数据截止时间和宽限期剩余天数。

打开 efdsearch.senate.gov → 勾选同意条款 → Report Type 选 Periodic Transactions → 起始日期填上次截止日 → 逐份打开并另存为网页（仅 HTML） → 上传到 senate-inbox 分支的 inbox/ 目录。

起始日期使用 Issue 中的最新申报日期，以覆盖当天；浏览器保存的 HTML 应保留来源网址。如果浏览器没有保留，请把报告网址中的编号加在文件名中。

纸质 PTR 上传完整 PDF，或一次上传全部页面图片。文件名：`报告编号__YYYY-MM-DD__申报人姓名.pdf`；图片为 `报告编号__YYYY-MM-DD__申报人姓名__页码.png`（也支持 jpg、gif、tif）。编号从报告网址复制，日期和姓名照搜索结果填写。

上传提交自动通知 code 分支的受控 Senate 工作流；原件标注 `acquisition: manual_browser` 并保存到 evidence，再复用现有解析、资格判定、候选、组装及正式发布流程。报告编号已完成手动导入时跳过；逐文件失败只影响该文件。成功导入后 Senate 截止时间推进，现有 PTR 与年报持仓继续保留。

每次导入在提醒 Issue 中回复报告数、合格交易数（按报告计数，不代表净新增）、重复报告和无法识别文件及原因。原件已归档后自动清空 inbox/，仅保留 `.gitkeep`；全部文件成功或已跳过重复时关闭 Issue。无法识别的原件也保存在 evidence，Issue 保持打开；按反馈修正文件后重新上传。工作流本身失败时保留 inbox/，维护者可在 Actions 网页重跑。下一周复用并重新打开同一提醒 Issue。
