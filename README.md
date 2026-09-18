# 政客交易数据后端

当前为 **0.2.0 公开 GitHub 路线的可运行原型**，依据 [总体设计 v0.3](docs/政客交易数据服务-总体设计.md) 和对方提供的 `hash-sharded-v2` 契约实现。House Clerk 年度索引、PTR 原件归档、电子 PTR 多版式抽取、官方人物身份建议和人工复核门禁已经可运行；扫描件、Senate/OGE 采集和对方未交付的新版消费端测试尚未完成。

## 已能运行的链路

规范记录 → 校验原始处理器 → 内容寻址分片 → Git 原子发布 → 先固定公开仓库 commit → 按该 commit 读取所有分片 → 原始处理器加工 → 原始渲染器生成 HTML。

- `backend/`：Python 3.11+ 标准库生产器，不依赖在线数据库。
- `client/`：公开 GitHub 直接读取适配器；首发主路径。
- `gateway/`：以后切回私有仓库时使用的可选只读代理；不在首发路径。
- `review-input/`：对方原始材料，只读基线，处理器和渲染器按 SHA-256 固定。
- `docs/`：总体设计、契约审阅、实现状态和维护说明。
- `.local/`：本机演示仓库与页面，不提交 Git。

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

公开读取命令见 [client/README.md](client/README.md)。`publish.yml` 已实现手动生产发布、远端基线比较以及分支和发布标签的原子推送；由于正式采集器还未完成，现在没有开启 schedule。

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
python -m unison_snapshot create-house-ptr-review --extraction .local/house-20035420-extraction.json --output .local/house-20035420-review.json
```

当前黄金样本集包含 5 份官方电子 PTR、共 24 行，覆盖普通股票、期权、无 ticker、金额换行、跨页行和 amended 申报。`20035420` 的身份建议为 `house:D000032`。所有结果仍为 `awaiting_manual_review`；复核记录必须填写身份依据、来源使用批准、逐行决定和具名复核者。amended 行还必须明确关联待替换的内部旧记录，或注明为何只能作为独立更正保留，才能通过 `promote-house-ptr-review`。具体见 [House PTR 复核流程](docs/House-PTR-复核流程.md)。

增量规划器会保留失败重试、发现官方索引字段变化或消失，并从已有内容寻址归档恢复完成状态。当前真实 2026 索引规划得到 393 份 PTR，其中 5 份已归档、388 份待处理、0 个索引异常。运行和故障处理见 [House PTR 增量运行](docs/House-PTR-增量运行.md)。

## 进入真实数据之前

需要继续扩充 House 扫描件、撤回件等黄金样本，把现有增量计划器接成受控下载/解析批次，再接入 Senate/OGE；同时取得对方新版消费代码，并验证无行情和覆盖不完整时的页面行为。详情见 [实现状态](docs/实现状态与下一步.md)。代码公开在 [hunterhigh/us-politician-trades-data](https://github.com/hunterhigh/us-politician-trades-data)；尚未配置生产密钥、购买 API、部署 Cloudflare 或修改现有 10 大 V 仓库。
