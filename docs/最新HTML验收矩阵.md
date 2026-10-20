# 最新 HTML 验收矩阵

## 2026-10-19 获批开口金额的本地检查

用户已批准交易和持仓采用有限／开口分开展示。原指定 HTML 及冻结模板哈希未变；`frontend-contract-v3` 只实施该明确变化，原 v1/v2 可继续读取旧版本。

| 编号 | 行为 | 已实际检查 | 当前边界 |
| --- | --- | --- | --- |
| H12 | 开口交易保留次数、阈值、无上限；有限合计和上界排名单独处理 | Chrome 检查混合两笔的 30/90 日块、人物、股票、详情抽屉；合计仅 $1,000,001–$5,000,000，另列超过 $50,000,000；空收益仍 null | 合成接口例用于语义；不作为 OCR 准确性或正式新增证明 |
| H13 | 开口持仓同样分开，不造上限、不混报告期 | Auchincloss 真实固定缓存经解析、资格、候选、最终 builder、Chrome 人物页：97 项，96 有限、1 配偶超过 $1,000,000；展开可见全部 97 项 | 18 条无期末价值明确排除；部分报告选择仍待 T19 决定 |
| H14 | 发布前浏览器门禁沿批准模板检查，不重套错误的旧金额显示 | 同一份 2026-09-20 缓存的 9,356 笔有限交易，原始模板与获批派生模板分别运行完整既有矩阵，均通过 | 本地固定旧范围回归，新增检查 0、未与最新生产 board 对比；不是 T25 最终全范围验收 |
| H15 | 2026-10-20 获批 House 新纸表确认持有人空白时显示 Undisclosed，合格披露行计数，不混为 Self | 合成两笔记录经既有 v3 消费／渲染到 Chrome：人物页两行及详情抽屉均显示 Undisclosed，脚本错误0；沿用原 ownerLabel 的原值显示，无需修改冻结 HTML | 仅检查页面语义；原件确为空白的来源证明及候选资格另有检查；不代表真实9115808已恢复交易，不批量改旧正式owner |

派生模板由门禁从仓库中哈希受控的渲染代码重建，不信任输入 HTML 的任意页面代码。没有开口政策的旧数据仍使用原模板。检查脚本为 `frontend-e2e/tests/open-amounts.e2e.mjs` 和 `latest-html-production.e2e.mjs`。


## 2026-10-15 账户标题待核接线后的完整页面回归

[Senate 纸表运行 38675534141](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38675534141) 的远端投影保留 36 行的 9 个核读账户标题，临时候选读入 483 个待核格、正式新增 0；该运行因五份旧解析失败在最终门禁标红。[完整生产运行 38676238093](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38676238093) 随后成功，候选与准备提交回读的两次最新 HTML 浏览器检查均 `status=passed`、`transactions=18491`、`new_transactions_checked=0`、`previous_board_compared=true`；旧事实差异 `change_count=0`，固定提交回读及原子发布成功。`main=e9a89b4a770783b4bd62ac5cc6c81767e3521f38`、`market=5f38bf734203d6c1a3100f69d5a43135e970428a`，正式仍为 401 人、18,491 笔交易、5,595 条持仓。

最新 HTML 原件 SHA-256 仍为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`；七个早于可验证行情首日的收益继续 `null`／“—”。本轮没有新正式交易，所以页面通过证明旧页面未回退，**不能**充当这 36 行的资产说明和披露行计数验收；该项将在身份、旧抽取关系与行资格闭合并生成正式新增候选后，按本矩阵 H04、H05、H10 执行。

## 2026-10-15 Senate 待核接线后的页面验收

用户指定 HTML 原件 SHA-256 本轮仍为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`；七个早于可验证行情首日的收益保持 `null`／“—”。[Senate 自动运行 38671326928](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38671326928) 已把 483 个待核格送入临时候选构建审计，首份 36 格有账户标题，来自投影的正式交易 0；五份旧失败报告仍触发最终正式门禁。

[完整生产运行 38672040462](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38672040462) 的两次最新 HTML 浏览器检查均通过，均为 `transactions=18491`、`new_transactions_checked=0`、`previous_board_compared=true`；旧事实差异 `change_count=0`，准备提交固定回读和原子发布成功。正式数据为 401 人、18,491 笔交易、5,595 条持仓。此前相对 18,467 增加的 24 笔由[较早的生产运行 38661794576](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38661794576)发布；本轮 Senate 接线净新增 0，因此本轮页面通过只证明既有页面没有回退，不代表 36 个待核披露行已在前端逐条验收。此前章节的 18,467 是当时快照，不再是当前正式计数。

## 2026-10-14 关键格复读接入后的完整页面验收

[完整生产运行 38547767845](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38547767845) 成功。来源层新增 Senate 日期格复读、House 争议格只读复读及 OGE 封面日期角色观察后，正式候选和准备提交固定版本回读均使用用户指定最新 HTML 的页面模板做浏览器检查；两次均 `status=passed`、`transactions=18467`、`new_transactions_checked=0`、`previous_board_compared=true`。旧正式事实差异 `change_count=0`、问题清单 0；页面仍为 401 人、18,467 笔交易、5,595 条持仓。原子发布后 `main=bc2664510bc20d48a79c89d8274f8a3e07c5924f`、`market=955327505e7c95cc551ed42ab3cc0fd4c04576c3`，远端指针独立回读一致。此次使用已发布行情缓存，没有请求新增 Twelve Data 额度。

最新 HTML 原件 SHA-256 为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`，继续高于旧交接材料；七个早于可验证行情首日的收益保持 `null`／“—”。本次新增正式交易为 0，因而这两轮页面检查证明旧页面没有回退，**不**证明 Senate 483 个待核格、House 134 行隔离或 OGE Part 7 已在前端逐条验收。

## 2026-10-14 待核投影接入后的完整生产验收

[完整生产运行 38536662369](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38536662369) 成功，候选构建、发布前最新 HTML 浏览器检查、旧正式事实差异、固定提交远端回读、第二次最新 HTML 浏览器检查和原子发布均执行。两次 `test:latest-html` 均返回 `status=passed`、`transactions=18467`、`new_transactions_checked=0`、`previous_board_compared=true`。正式仍为 401 人、18,467 笔交易、5,595 条持仓；没有把 Senate 483 个待核格或 OGE Harris 95 个文字层交易形状变成新交易。发布后 `main=e4e581c00e37d85e5851abdcb001c7d1994520ea`、`market=416afb3edcafa79548d54d46ea0b84943f5450cc`，独立回读 `main` manifest 的 `market_commit` 与 market 指针一致。该次用已发布行情缓存，未请求新增 Twelve Data 额度。

最新 HTML 原件 SHA-256 再核对为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`，仍为最高页面基准。七个早于可验证行情首日的收益保留 `null`／“—”。两轮页面通过说明既有正式页面没有因旁路投影退步；由于新增交易数为 0，不声称新隔离记录已经通过页面逐条验收。

## 2026-10-14 结构接入后的实际页面门禁

[完整生产运行 38529268285](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/38529268285) 成功。候选构建、发布前浏览器检查、旧正式事实差异、预备提交远端回读、第二次浏览器检查和原子发布均通过。两次 `test:latest-html` 均报告 `status=passed`、`transactions=18467`、`previous_board_compared=true`、`new_transactions_checked=0`。这次没有新增可供逐条新交易页面验收的正式事实；其通过只证明现有页面结果未因旁路解析观察退步。正式 `main=28a5f94e7126d9e4acdb0c5315e586a36c2e5021`、`market=c16ab6f6f7d53af04be8b20cfff90d9cbb47ca8c`，独立回读 manifest 的 `market_commit` 与 market 指针一致。正式数量 401 人、18,467 笔交易、5,595 条持仓。用户指定 HTML 文件 SHA-256 于本次运行前复核仍为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。七个早于可验证行情首日的收益仍按 `null`／“—”验收，不用旁路格位填值。

基准：用户指定的 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`（2026-10-09 复核）。冲突时以此 HTML 的实际页面代码及用户对七个不可验证收益值的 `null`／“—”裁定为准。`review-input/` 只作历史兼容对照。

验收入口：`frontend-e2e/tests/latest-html-production.e2e.mjs` 将生产候选 DATA 注入从该 HTML 提取且哈希固定的页面模板，由 Chromium 执行页面代码。完整发布在候选构建后和固定提交回读后各运行一次。`PREVIOUS_MAIN_ROOT` 指向发布前固定 `main` 工作树，用其 manifest 和 board 识别新增交易。原 HTML 的只读交互基准另由 `latest-html.e2e.mjs` 核对文件哈希和页面行为。

| 编号 | HTML 实际消费与判定 | 可执行断言／证据 | 边界 |
| --- | --- | --- | --- |
| H01 | 看板两个 30/90 日块同时存在；顶部窗口按钮为滚动锚点 | 原 HTML 只读浏览器审计；生产模板检查两个块 | 不把锚点误作互斥筛选 |
| H02 | 交易时间线按 `transaction_date` 的包含截止日 30/90 日窗口；按 `filed_at` 降序；两个块的搜索和方向筛选独立 | 两窗口实际 DOM ID 集合和顺序与候选字段独立计算逐项相等；30 日搜索和购买方向筛选后核对行数，90 日块不随搜索改变 | 越界记录不应混入窗口 |
| H03 | Capitol Hill Watch 按 `filed_at` 的包含截止日 30/90 日窗口 | 页面 `windowDisclosures()` 返回 ID 与独立计算相等 | 与 H02 的时钟分开 |
| H04 | 时间线人物、资产、方向、持有人、金额上下界、申报时间；详情抽屉展示原记录日期、区间及官方证据 URL | 真实窗口行进入抽屉，逐字段比对候选；来源链接按 `href` 比对 | 金额未知不得由构建器填成零；该质量门禁在候选资格层 |
| H05 | 人物页、股票页和抽屉导航；人物 ID、ticker 关联 | 浏览器打开人物及证券页面；新增近 90 日交易逐条在人物表和时间线中可达 | 无 ticker 交易仍留在披露与人物路径 |
| H06 | 30/90 日热门股票消费各自 `market_moves`，其值是证券从窗口首笔交易日至行情截止的价格变化 | 两个热门股票 DOM 对应窗口行情投影逐项核对可计算收益或“—” | 不解释为官员实际交易收益 |
| H07 | 股票页价格历史图使用对应 ticker 的已发布日线，并标注行情截止与来源 | 检查 SVG 价格线、末点、ticker 标签、行情截止；无行情证券检查空图和“—” | 不补造缺失历史价格 |
| H08 | 来源卡只显示白名单中的官方披露来源；成功同步、来源截止、检查时间按 HTML 优先序，非 `ok` 显示 Delayed | 逐来源比对卡片 URL、状态、展示日期；行情健康行不作为披露卡 | 最后检查时间不得冒充成功同步 |
| H09 | 缺 ticker、缺行情、缺收益按页面空状态显示；事件日在可验证行情首日前的收益不可计算 | 缺 ticker 抽屉、存在无行情 ticker 时的股票空图、全部可识别的前史收益 `null`；最多七个抽屉样本显示无涨跌颜色的“—” | 原 HTML 样本的七个错误数值由用户决定覆盖；当前生产候选出现几项依实际输入而定 |
| H10 | 相邻快照新增交易要抵达最新 HTML 页面 | 与发布前固定 board 按 ID 比较；每条新增交易打开详情并核对金额、日期、证据；近 90 日者还检查时间线及人物表 | 无新增时记录 0，不声称真实增量已验收；历史交易不强制出现在 90 日窗口 |
| H11 | 生产候选和准备提交回读使用同一页面代码验收 | `publish-complete.yml` 两次运行 `test:latest-html`；回读固定 `main/market` 提交 | 发布失败与回滚另有独立验收，不由页面矩阵证明 |

矩阵的完整通过条件是固定 HTML 哈希、候选与回读两次浏览器门禁均通过，且当次新增记录数与逐条页面可达检查数一致。页面断言失败先进入待核清单并保留候选；影响正式事实的未决差异在最终发布门禁阻断。定向原件核验仍留在构建和页面检查之后，不触发全量历史重扫。

本矩阵只覆盖最新 HTML 的实际消费路径。五数组守恒、来源资格、行情许可、原件归档、失败不移动指针、真实定时增量和回退须分别有自己的运行证据，不能由本矩阵的浏览器通过替代。

## 本轮验证与待补证据

2026-10-09：原 HTML 只读浏览器审计通过，哈希仍与基准一致。针对新断言，用原 HTML 的页面和数据制作了临时浏览器夹具；仅在此临时夹具中将早于其展示行情首日的收益设为 `null`，并通过移除一条旧 board ID 模拟一个新增记录。`test:latest-html` 分别以新增 0 和 1 条通过，后者确实检查了该条的抽屉、时间线和人物页；另有 8 项相关后端测试通过，脚本语法、工作流 YAML 和 `git diff --check` 通过。该临时夹具不属于正式候选，其后真实候选运行证据单独记录如下。

同日[完整生产运行 37878827389](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/37878827389) 已让首轮真实候选页面检查逐条通过 44 条新交易；固定提交回读后的第二轮完整页面检查也通过。该轮第二次检查的 `new_transactions_checked=0`，原因是此前传入的旧 `main` 工作树在准备提交时已前移，不能用来重算新增数。现已在工作流构建前复制固定旧 manifest/board，并让两轮均使用这一独立副本。此修正消除后续运行的比较基线漂移；本轮 44 条的第二次逐条检查仍无实跑证据，不能倒填为已通过。
