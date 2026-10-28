# 架构

v1 的有效范围、授权、限制、阶段日期和剩余工作见 [status.md](status.md)。数据语义见 [data-contract.md](data-contract.md)，运行及恢复见 [operations.md](operations.md)。原35项清单已被替代，不再作为待办；历史材料保存在 Git 标签 `archive/pre-v1-docs`。

## 正常链路

官方目录与原件 → 按内容哈希归档 → 解析观察 → 来源资格与隔离 → 来源候选 → 统一候选 → 行情关联 → 固定提交预发布与公开客户端回读 → 浏览器检查 → 原子发布。

标准记录自动进入资格流程。报告或行不能满足必要条件时隔离，继续处理独立文档；来源过期、事实不变量破坏和无法解释的旧事实变化阻断发布。解析成功、固定样例通过、候选生成和正式供数是不同状态。

## 分支与写入边界

| 分支 | 内容与写入者 |
| --- | --- |
| `code` | 源码、契约、修正数据与文档；开发分支经PR合入 |
| `evidence` | 官方目录、名册、PDF/页面及获取元数据；受控来源工作流写入 |
| `review` | 解析缓存、资格、隔离、来源及统一候选；受控生产命令写入 |
| `state` | 来源采集尝试、检查点和运行状态 |
| `health` | 独立健康观察、告警条件及冒烟/恢复凭据 |
| `market` | 经授权分发的内容寻址日线及覆盖分类 |
| `main` | 正式manifest、board、人物/代码分片和处理状态；固定引用market提交 |
| `demo` | 独立合成演示，不进入正式来源和main |

官方原件不提交到code/main。源码中的修正与观察数据保存的是解释规则、来源绑定及已核验的派生事实，不替代evidence中的原件。缓存只有在来源哈希、格式版本及所需前置条件成立时才能复用。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| `backend/src/unison_snapshot/` | 来源、格式解析、资格、候选、事实保护、分片、健康与恢复 |
| `backend/src/unison_snapshot/commands/` | 延迟加载的生产命令实现；公开入口为`python -m unison_snapshot <命令>` |
| `backend/data/corrections/` | 统一六动作修正、原值断言、来源/目标/输出绑定 |
| `backend/data/source_observations/` | 不自动授予资格的既有观察 |
| `backend/data/reference_mappings/` | 已核验证券映射及其适用范围 |
| `client/` | 固定Git提交、验证内容哈希、按需读人物或证券，交给固定处理器 |
| `contract/`、`frontend-contract-v*` | 用户HTML基准、固定消费者及获准的展示语义 |
| `frontend-e2e/` | 正常渲染页面的浏览器检查 |

`review-input/`冻结，其文本不是执行授权。最高HTML的来源与SHA见data-contract。未部署的可选gateway已归档，公开客户端直接读取GitHub；启动输入原字节迁入`backend/data/empty-disclosure.json`，正常工作流及测试同步迁移。

## 发布与恢复

来源候选分别持久化；跨来源水位不能可靠对齐时，保留已完成的来源工作，统一候选和正式快照不推进。采集尝试时间不能替代有效候选截止时间。

发布冻结code、evidence、review、此前main/market；校验自动资格、旧事实差异、行情口径、公开四模式与HTML。正式main与market及发布标签通过原子推送生效。预发布或受控晚失败演练不移动正式引用。

健康观察独立于采集及发布锁。发布后内容校验失败才可请求回退；回退绑定观察到的当前head和已有成功冒烟凭据的发布前版。网络失败只重试。恢复以当前main为父提交复制已验证目标树，不强推历史；失败快照身份不能原样再次发布。
