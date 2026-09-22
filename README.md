# 政客交易数据后端

这是公开 GitHub 上运行的政客交易数据生产后台。首发五数组和冻结前端验收已完成；当前工作是稳定定时增量、控制行情重取成本、维护覆盖审计与恢复能力。执行优先级以[当前目标与优先级](docs/当前目标与优先级.md)为准，实测版本、空缺和下一步以[实现状态与下一步](docs/实现状态与下一步.md)为准。设计依据见[总体设计](docs/政客交易数据服务-总体设计.md)。

## 数据怎样流动

官方 House、Senate、OGE 披露 → 原件归档 → 确定性解析与自动资格判定 → 各来源候选合并 → Alpaca SIP 行情及覆盖分类 → 冻结前端预验收 → 先发布 `market`、再发布固定引用它的 `main` → 固定提交回读与浏览器复验。异常事实进入隔离，不会被改成合格事实；任一发布门禁失败时保留上一个正式快照。

## 目录地图

| 位置 | 职责 | 当前使用方式 |
|---|---|---|
| `backend/src/unison_snapshot/` | 官方来源采集、解析、自动资格、候选、行情、分片、发布与回退 | 生产核心；`backend/tests/` 和 `backend/scripts/verify.py` 验证行为 |
| `.github/workflows/` | 来源增量、完整发布、回退和离线契约检查 | GitHub Actions 生产入口；完整发布为工作日定时和手动触发 |
| `client/` | 解析公开仓库的固定提交，按内容哈希读取快照 | 首发消费路径 |
| `frontend-e2e/` | 使用冻结交接包的处理器、渲染器和浏览器链路验收 | 发布前后门禁；也有离线合成测试 |
| `gateway/` | 私有仓库场景的可选只读 Worker | 已实现并测试，当前公开读取路径不依赖它 |
| `docs/` | 当前目标、实测状态、设计、生产配置和运行说明 | 先读目标和状态；历史阶段数字不当作实时状态 |
| `review-input/` | 对方交接包与交接说明 | 冻结只读消费契约，不在本项目改写 |
| `ingest/`、根目录`manifest.json`与`board/` | 首发前的启动快照和输入示例 | `code`分支上的历史样本；正式快照只看`main`，不可把这里的`bootstrap_empty`当成生产现状 |
| `.local/` | 本地演示和临时产物 | 不提交 Git |

## 生产分支

| 分支 | 内容与写入边界 |
|---|---|
| `code` | 源码、工作流和文档；开发从短分支经 PR 合入 |
| `evidence` | 官方原件及获取元数据；只由受控来源工作流写入 |
| `state` | 来源检查点和运行状态 |
| `review` | 解析、资格、隔离和候选数据；不等于正式发布 |
| `market` | 经授权发布的内容寻址日线；`main` 固定引用其提交 |
| `main` | 正式非演示快照的 manifest、board、people 和 tickers |

生产入口是 [main/manifest.json](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/main/manifest.json)。实时数据、运行结果和未覆盖范围以固定提交及[实现状态与下一步](docs/实现状态与下一步.md)核对。`universe_complete=false` 表示尚未覆盖全部美国政客与全部历史披露；缺少可核验行情的代码保持无价，不填零。

## 本地验证

需要 Python 3.11+ 与 Git。核心回归：

```powershell
Set-Location backend
python scripts/verify.py
```

浏览器契约验收需要 Node 24 与系统 Chrome、Chromium 或 Edge：

```powershell
Set-Location frontend-e2e
npm ci
npm test
```

本地合成演示从项目根目录运行 `python backend/scripts/demo.py`，只写 `.local/` 的 demo 数据，不进入正式 `main`。公开读取命令见 [client/README.md](client/README.md)，来源运行见 [House PTR 增量运行](docs/House-PTR-增量运行.md)，生产开关、Secrets 与发布说明见[生产配置](docs/生产配置.md)。

本项目不修改现有 10 大 V 仓库。交接包与交接说明共同构成唯一消费契约；交接说明自述的 57 项测试不计入本项目实测结果。
