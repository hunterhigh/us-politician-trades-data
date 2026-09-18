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

生产部署使用同一公开仓库的五个持久面：默认分支 `code` 保存源代码、Actions 和文档；`main` 只追加正式 `manifest/board/people/tickers` 快照；`state` 保存来源检查点和运行状态；`evidence` 按 SHA-256 保存官方索引 ZIP、议员名册、原始 PTR PDF 和获取元数据；`review` 保存机器抽取、确定性身份、自动资格结果、异常和隔离记录。密钥只存放于 GitHub Actions Secrets。

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

## 命令行接口

在 `backend/` 下设置模块路径后可发布输入文件和重建某个版本：

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m unison_snapshot publish-demo --input examples/synthetic.json --store ../.local/manual.git --generated-at 2026-09-18T00:01:00Z
python -m unison_snapshot assemble --store ../.local/manual.git --commit <上一步返回的40位提交值> --mode people --key house:DEMO001 --output ../.local/person.json
```

`client/snapshot_repo.py` 是本项目给出的公开 GitHub 读取实现，仍不是对方声称已经完成但尚未提供的那份文件。只有本项目测试及原包处理器和渲染器获得实际验证；不宣称对方新版 57 项测试已通过。

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

增量规划器会保留失败重试、发现官方索引字段变化或消失，并从已有内容寻址归档恢复完成状态。截至 2026-09-18 的生产检查点，真实2026索引规划得到393份PTR，其中130份已归档、263份待处理、0个下载失败、0个索引异常。已归档原件中121份抽取成功，110份产生1248条候选交易，29条异常记录被隔离；9份旧式纸面勾选表等待专用表格解析器。动态状态以 [`state/status/last-run.json`](https://github.com/hunterhigh/us-politician-trades-data/blob/state/status/last-run.json) 和 [`review/status/summary.json`](https://github.com/hunterhigh/us-politician-trades-data/blob/review/status/summary.json) 为准。运行和故障处理见 [House PTR 增量运行](docs/House-PTR-增量运行.md)。

## 当前交付目标

继续完成 House 原件、OCR和解析回填，接入 Senate/OGE 与获准公开生产使用的行情源，构建完整候选快照，再用对方实际前端代码和全部测试完成验收。前端功能和数据含义不为后台现状降级。详情见[当前目标与优先级](docs/当前目标与优先级.md)和[实现状态](docs/实现状态与下一步.md)。代码公开在 [hunterhigh/us-politician-trades-data](https://github.com/hunterhigh/us-politician-trades-data)；行情密钥和许可尚未配置，未购买 API、部署 Cloudflare 或修改现有 10 大 V 仓库。
