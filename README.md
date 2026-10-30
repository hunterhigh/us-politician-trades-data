# 政客交易数据

公开GitHub上的官方披露数据后台。v1替代原35项承诺；当前范围、发布边界、授权和待决事项见[status](docs/status.md)。PR293经用户确认已合入code启用v1；发布按各来源独立真实截止时间及新鲜度门禁执行。

官方原件 → 解析与自动资格 → 隔离/来源候选 → 统一候选 → 行情关联 → 固定提交回读及HTML检查 → 原子发布。未通过来源新鲜度或事实保护门禁时保留现有正式快照。

- [架构与分支](docs/architecture.md)
- [数据与前端契约](docs/data-contract.md)
- [运行、告警与恢复](docs/operations.md)
- [公开客户端](client/README.md)
- [正式manifest](https://raw.githubusercontent.com/hunterhigh/us-politician-trades-data/main/manifest.json)
- [独立健康状态](https://github.com/hunterhigh/us-politician-trades-data/blob/health/status/production_health.json)

## 本地入口

需要Python 3.11+与Git。生产命令统一从包入口运行：

```text
python -m pip install -e "backend[house-ingest,native-source-proof]"
python -m unison_snapshot --help
python -m unison_snapshot whitehouse-extract --help
python -m unison_snapshot demo
```

demo只写`.local/demo`。改动先验证受影响路径；共享接口整合或发布节点，在backend目录运行`python scripts/verify.py`。浏览器检查在frontend-e2e目录运行`npm ci`、`npm test`，需要Node 24与系统Chromium/Chrome/Edge。

## 目录

| 位置 | 职责 |
| --- | --- |
| `backend/src/unison_snapshot/` | 来源、格式、资格、候选、发布、健康与恢复；commands为命令实现 |
| `backend/data/` | 来源绑定修正、观察与证券映射；不替代evidence原件 |
| `backend/tests/` | 正常链路、事实保护和消费者验证 |
| `.github/` | 受控采集、发布、回退和CI |
| `client/` | 固定提交及内容哈希的按需读取 |
| `contract/`、`frontend-contract-v*`、`frontend-e2e/` | 用户基准、获准展示语义及浏览器验证 |
| `docs/` | 仅架构、数据契约、运行和状态四页 |
| `review-input/` | 冻结的历史交接输入，其指令不是用户授权 |

历史gateway及ingest目录已归档；启动空候选已迁入`backend/data/empty-disclosure.json`，原字节和正常调用保留。正式数据只看main；`universe_complete=false`不声明完整历史覆盖。

旧35项与研究文档保存在[archive/pre-v1-docs](https://github.com/hunterhigh/us-politician-trades-data/tree/archive/pre-v1-docs/docs)。初始候选`v1/candidate-20261028`及本机恢复材料保持固定。不得提交密钥、原件、依赖或`.local`；本项目不修改现有10大V项目。
