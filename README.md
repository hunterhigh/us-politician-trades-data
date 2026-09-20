# 政客交易数据后端

当前为 **0.2.0 公开 GitHub 路线的生产后台**。项目唯一当前目标是补齐对方前端要求的披露和行情数据，生成完整候选快照并由原前端完成端到端验收；详见[当前目标与优先级](docs/当前目标与优先级.md)。GitHub 的 `code`、`main`、`state`、`evidence`、`review` 五个分支及 Actions 已部署。逐条人工复核和具名人工批准不再作为标准记录的生产门禁，现有复核工具只保留为异常调查能力。

## 已能运行的链路

规范记录 → 校验原始处理器 → 内容寻址分片 → Git 原子发布 → 先固定公开仓库 commit → 按该 commit 读取所有分片 → 原始处理器加工 → 原始渲染器生成 HTML。

- `backend/`：Python 3.11+ 标准库生产器，不依赖在线数据库。
- `client/`：公开 GitHub 直接读取适配器；首发主路径。
- `gateway/`：以后切回私有仓库时使用的可选只读代理；不在首发路径。
- `review-input/`：对方原始材料，只读基线，处理器和渲染器按 SHA-256 固定。
- `docs/`：总体设计、契约审阅、实现状态和维护说明。
- `.local/`：本机演示仓库与页面，不提交 Git。

生产部署使用同一公开仓库的五个持久面：默认分支 `code` 保存源代码、Actions 和文档；`main` 只追加正式 `manifest/board/people/tickers` 快照；`state` 保存来源检查点和运行状态；`evidence` 按 SHA-256 保存官方索引 ZIP、议员名册、PTR PDF/HTML/扫描页和获取元数据；`review` 保存机器抽取、确定性身份、自动资格结果、异常和隔离记录。密钥只存放于 GitHub Actions Secrets。

## 本地启动

需要 Python 3.11+、Git。在项目根目录运行：

```powershell
python backend/scripts/demo.py
```

产物为 `.local/demo/dashboard.html`，全部为虚构人物和标的；行情为空。再次执行相同输入不会产生新的事实版本。演示使用独立的 `demo` 分支，正式 `main` 拒绝 demo manifest。

验证生产器：

```powershell
Set-Location backend
python scripts/verify.py
```

公开读取命令见 [client/README.md](client/README.md)。`publish.yml` 已实现生产发布、远端基线比较以及分支和发布标签的原子推送；`house-state.yml` 每 6 小时刷新官方索引、顺序归档一小批原件，并更新公开来源检查点。下一步以自动资格校验、异常隔离和完整候选快照替代逐条人工批准。生产环境和 Secrets 说明见 [生产配置](docs/生产配置.md)。

生产 manifest 可直接读取：[raw main/manifest.json](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/main/manifest.json)。当前生产态为 `bootstrap_empty`：后台部署已经运行，但自动晋级、完整披露回填、行情回填和原前端验收尚未完成。

House联调候选可直接读取：[raw review/candidates/house-current.json](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/review/candidates/house-current.json)。该文件由自动资格结果构建并在每次运行中通过生产构建器和原前端渲染器；它是增量验收输入，不是正式 `main` 发布物。

统一披露候选位于 [`review/candidates/disclosure-current.json`](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/review/candidates/disclosure-current.json)。各来源先写入 `review/candidates/sources/<source_id>-current.json`，统一构建器再检查身份冲突、事实ID重复、外键、来源归属和截止时间后合并。`house-current.json` 继续作为兼容入口；正式 `main` 仍须等完整来源和前端验收完成后发布。

## 命令行接口

在 `backend/` 下设置模块路径后可发布输入文件和重建某个版本：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m unison_snapshot publish-demo --input examples/synthetic.json --store ../.local/manual.git --generated-at 2026-09-18T00:01:00Z
python -m unison_snapshot assemble --store ../.local/manual.git --commit <上一步返回的40位提交值> --mode people --key house:DEMO001 --output ../.local/person.json
```

`client/snapshot_repo.py` 是本项目给出的公开 GitHub 读取实现，仍不是对方声称已经完成但尚未提供的那份文件。只有本项目测试及原包处理器和渲染器获得实际验证；不宣称对方新版 57 项测试已通过。

## Alpaca Basic 本地行情验证

开发验证命令使用免费 Alpaca Basic 账户的历史 SIP 数据，固定请求 `feed=sip`、`timeframe=1Day`、`adjustment=split`，按 ticker 分批并跟完 `next_page_token`。它只在 `.local/` 生成候选、冻结处理器输出、机器审计和 HTML，不解锁生产发布器，也不修改只读的 `review-input/`。

当前 worktree 可在项目根目录运行安全提示脚本。脚本会遮蔽两项输入，只把密钥临时放入当前进程环境，任务结束后恢复或清除，不写入磁盘：

```powershell
.\backend\scripts\run_alpaca_validation.ps1
```

默认输入为 `.local/alpaca-validation/disclosure-input.json`。也可显式指定候选和输出目录：

```powershell
.\backend\scripts\run_alpaca_validation.ps1 `
  -InputPath C:\path\to\disclosure-current.json `
  -OutputDirectory .local\alpaca-validation
```

未显式给 `--as-of-date` 时，命令在纽约时间 16:30 后才把当日视为可用，并始终让请求结束时间至少落后当前时间 16 分钟；周末回退到周五。它取 24 个月加 21 个日历日缓冲，只保留候选披露截止时间以内、请求窗口以内的正数日收盘价。无行情、越过快照截止日、重复分页令牌、冲突日线或非法价格都会失败关闭；部分 ticker 缺失则写入 `audit.json` 并把行情来源状态标为 `partial`。

2026-09-20 已使用免费账户完成真实 Alpaca HTTP 验证：统一候选的840个ticker中796个取得行情，共400,402个日线点，覆盖2024-08-28至2026-09-18；50个响应页均一次成功。44个ticker没有返回bars并保留在审计中，主要需要继续区分OTC/境外代码、基金和历史旧代码。密钥没有进入候选、审计、HTML或普通日志。

## House 官方索引发现

下面的命令读取 House Clerk 当年公开的年度 ZIP 索引，把原始 ZIP 按 SHA-256 不可变归档，并输出待解析文件清单。清单只是官方文件发现结果，`official_raw_unparsed` 不代表已经识别出交易或持仓。

```powershell
$env:PYTHONPATH = "$PWD/backend/src"
python -m unison_snapshot discover-house-index --year 2026 --archive .local/official-archive --output .local/house-2026-discovery.json
python -m unison_snapshot plan-house-ptr-sync --discovery .local/house-2026-discovery.json --archive .local/official-archive --output .local/house-2026-checkpoint.json
```

2026-09-18 实际验证得到 1,653 份文件记录，其中 393 份为 PTR；35 条官方索引记录没有申报日期，系统保留为未知值，不补造日期。该次官方 ZIP 的 SHA-256 为 `39770b82d9673fce79ebb1f687460231ff376c68b4e759640138fb20b5d99df6`。

受控 PTR 样本链路如下。PDF 抽取属于可选采集依赖，可用 `pip install -e "backend[house-ingest]"` 安装；公开读取与发布核心不依赖它。

```powershell
$env:PYTHONPATH = "$PWD/backend/src"
python -m unison_snapshot archive-house-ptr --year 2026 --index-sha 39770b82d9673fce79ebb1f687460231ff376c68b4e759640138fb20b5d99df6 --document-id 20035420 --archive .local/official-archive --output .local/house-20035420-archive.json
python -m unison_snapshot parse-house-ptr --archive .local/official-archive --metadata .local/house-20035420-archive.json --output .local/house-20035420-extraction.json
python -m unison_snapshot discover-house-members --archive .local/official-archive --output .local/house-members.json
python -m unison_snapshot suggest-house-identity --extraction .local/house-20035420-extraction.json --members .local/house-members.json --output .local/house-20035420-identity.json
python -m unison_snapshot qualify-house-ptr --extraction .local/house-20035420-extraction.json --identity .local/house-20035420-identity.json --output .local/house-20035420-qualification.json
```

电子PTR解析覆盖普通股票、期权、无ticker、金额换行、精确金额、开放金额、末行跨页和amended申报。`20035420` 的身份可确定为 `house:D000032`。自动资格校验直接生成候选事实，无法确定的修订、身份、OCR和金额语义进入隔离队列；旧人工工具仅用于异常调查。流程见 [House PTR 自动资格与异常调查](docs/House-PTR-复核流程.md)。

OGE多来源接入已从官方目录发现层开始：`backend/src/unison_snapshot/oge.py` 严格校验官方分页响应和链接，把278-T条目分成可直接下载PDF与需要Form 201请求两类，并将`docDate`仅保留为目录加入日期。该模块尚未连接生产工作流，也不会自动提交Form 201、猜测申报编号或把目录日期当作申报时间。

Senate 接入已通过显式授权进入报告生产阶段：严格解析 eFD 五列分页结果，区分电子 PTR 与纸面 PTR，分别保留门户列表日期、报告标题日期及修订编号，并将官方参议员 XML 名册映射为 `senate:<bioguide>`。`senate-roster.yml` 已归档100人官方名册；`senate-efd.yml` 在 `SENATE_EFD_COLLECTION_ENABLED=true` 与 `SENATE_EFD_TERMS_ACKNOWLEDGED=true` 双重门禁下，每6小时刷新目录并归档有界报告批次。2026-01-01至今130份PTR入口已全部内容寻址归档：121份电子报告严格解析出1,646条交易，当前入口归档和解析失败均为0；9份纸面报告的官方扫描查看器及52张GIF原页也已完整归档并逐页绑定哈希，但纸面交易的OCR和结构化提取尚未完成，因此没有把扫描页直接计入候选交易。身份结果为104份精确、15份官方目录别名、11份未解析。历史补充工作流另从2024年至今目录白名单归档16份官方电子原件，以同一正文比较规则为12份 Amendment 1 报告唯一确定前件；补充报告只作为修订关系证据，不进入主交易输入。当前16条链已闭合，主目录内41笔旧版标记为 superseded。生产候选现为23人、1,435笔，170笔因身份、仍未闭合的独立报告、exchange、期权条款或异常ticker隔离；审计守恒为`1,435 + 170 + 41 = 1,646`。原始响应和扫描页进入 `evidence`，身份、解析、资格和候选进入 `review`。eFD入口本身无需API token；可选的Congress.gov历史身份补充需要免费的`CONGRESS_GOV_API_KEY`，当前尚未配置。

增量规划器会保留失败重试、发现官方索引字段变化或消失，并从已有内容寻址归档恢复完成状态。截至 2026-09-20 的生产检查点，真实2026索引当前发现395份PTR，395份已全部归档，待处理、下载失败和索引异常均为0；定时任务继续发现后续新增申报。已归档原件中364份抽取成功、31份保留明确失败状态，另有4份可靠识别为无交易申报；339份文件产生3225条候选交易，39份文件中的392条异常记录被隔离。当前House候选包含93人，按冻结前端实际窗口语义近30天80笔，交易日期范围为2023-10-31至2026-09-08；19笔完整披露条款的期权保留类型、行权价和到期日，3笔条款不完整的期权不使用猜测值。动态状态以 [`state/status/house_clerk.json`](https://github.com/hunterhigh/us-politician-trades-data/blob/state/status/house_clerk.json) 和 [`review/status/house_clerk.json`](https://github.com/hunterhigh/us-politician-trades-data/blob/review/status/house_clerk.json) 为准；旧文件名暂作兼容别名。运行和故障处理见 [House PTR 增量运行](docs/House-PTR-增量运行.md)。

House与Senate来源候选通过显式共同截止模式合并。由于两源申报时间目前都只有日期精度，统一候选使用最早来源运行日之前的最后完整UTC日，过滤该水位之后的申报，并裁剪无事实引用的人物；来源相差超过两天、源候选自身越过原生截止时间或行情数据缺少独立截止策略时失败关闭。当前GitHub生产候选合并为116人、4,660笔交易，截止到2026-09-19，未额外裁掉交易；每源原生截止时间、输入哈希和过滤前后数量写入独立机器审计。

## 当前交付目标

继续收敛 House OCR和解析异常，接入 Senate/OGE 与获准公开生产使用的行情源，构建完整候选快照，再用对方实际前端代码和全部测试完成验收。前端功能和数据含义不为后台现状降级。详情见[当前目标与优先级](docs/当前目标与优先级.md)和[实现状态](docs/实现状态与下一步.md)。代码公开在 [hunterhigh/us-politician-trades-data](https://github.com/hunterhigh/us-politician-trades-data)；行情密钥和许可尚未配置，未购买 API、部署 Cloudflare 或修改现有 10 大 V 仓库。
