# 最新前端 HTML 消费契约审计

审计日期：2026-09-29
审计范围：只读检查用户指定的 HTML、`review-input/` 冻结消费材料、原处理器和渲染器。本文只记录观察与对照，不修改消费端语义，也不代表完整真实生产候选已经通过端到端验收。

## 1. 基准与计算口径

本审计将 `C:\Users\admin\Downloads\politician-disclosures (3).html` 作为最高优先级的前端实现基准。审计时文件存在，SHA-256 为：

```text
D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4
```

该哈希与用户指定的基准一致。任何与此 HTML 实际字段、页面行为或缺失值语义冲突的旧交接说明、交接包页面描述、仓库文档和附件计划，均按用户 2026-09-29 的最新决定处理；冲突不能反过来降低 HTML 的优先级。`review-input/` 仍是冻结对照材料，不直接修改。

样本快照的 `meta` 直接声明：`schema_version=politician-dashboard/v1`、`snapshot_id=19744d00fd3fa15249697ce3de7f21b29d1b6dc97cafdfa3a973b5194c127c74`、`generated_at=2026-09-29T02:18:01Z`、`data_cutoff_at=2026-09-27T23:59:59Z`、`timezone=America/New_York`、`default_window_days=30`、`source_policy=official_disclosures_only`、`market_price_policy=alpaca_sip_split_adjusted_completed_eod`，并含 `publication.market_commit=29251969fbadb06565781aa9c9d5bafd3ce6e00e`。

本次计数从 HTML 内嵌的 `const DATA = {...}` JSON 直接读取，以顶层数组长度作为记录数；HTML 文件大小为 68,817,061 bytes，内嵌 JSON 文本为 68,585,025 个 Unicode 字符、68,587,472 UTF-8 bytes。HTML 页面依赖通过剔除内嵌 JSON 后，对剩余脚本检查 `DATA.<field>` 和函数/属性引用得到。字段空值统计按每个数组记录的 JSON 字段值计数；未统计语义上“无法计算”以外的业务真值。

| 顶层字段 | 样本数量 / 形态 | 说明 |
|---|---:|---|
| `people` | 392 | canonical collection |
| `transactions` | 18,017 | canonical collection |
| `reported_holdings` | 5,593 | canonical collection |
| `security_market_data` | 1,748 | canonical collection |
| `source_health` | 5 | canonical collection；页面只消费符合白名单的披露来源 |
| `market_moves` | 30 日 44 条、90 日 539 条 | 由交易与行情派生的窗口投影；HTML 直接读取 |
| `recent_transactions` | 18,017 | 样本中记录及字段与 `transactions` 完全相同；页面脚本未直接读取 |
| `summary` | object | 页面脚本未直接读取 |
| `activity_by_day` | 440 | 页面脚本未直接读取 |
| `priority_people` | 1 | 页面脚本未直接读取；页面置顶卡由 HTML 自身按规则计算 |
| `top_people` | 6 | 页面脚本未直接读取 |
| `top_tickers` | 44 | 页面脚本未直接读取 |

“页面脚本未直接读取”只表示本 HTML 当前脚本无直接访问，不证明原处理器、其他消费者、问答链路或兼容测试不需要该投影。不得仅凭这项观察删除其规范生成或快照兼容性。

## 2. 五个 canonical collections：字段 × 页面矩阵

### 分类说明

- **必需（页面依赖）**：当前 HTML 的关联或模板直接依赖；若缺失，相关页面/交互会失败或失去关键内容。
- **可空 / 有降级**：HTML 对 `null`、空值、无 ticker、无行情或无映射有显式分支，或当前样本确有空值并有降级展示。
- **派生**：由原始字段、其他 canonical collection 或处理器/页面逻辑计算出来；不应被误认为来源直接披露事实。
- **仅审计 / 当前页面未消费**：用于来源追溯、投影资格或覆盖说明；或者该字段目前未被此 HTML 直接读取。未消费只限本 HTML 观察范围。
- **推断必需**：处理器可能未对该字段执行独立非空校验，但 HTML 模板直接插值/解引用，故根据页面代码推断实际展示数据须提供。

| Collection | 字段及分类 | 页面与消费方式 |
|---|---|---|
| `people` | `id`、`display_name`：必需；`short_name`、`party`、`role`、`state`、`priority`：页面展示/筛选属性，部分位置有回退或省略；`portrait_data_uri`：卡片/头像图片实际使用，空值会造成无图；`portrait_is_placeholder`、`portrait_source_url`：可空、决定头像标签和身份链接；`chamber`：部分页面展示或作为 `role` 回退；`disclosure_authority`、`office_type`、`portrait_url`、`priority_reason`：本 HTML 脚本未直接读取；样本中 `chamber/party/state` 各 113 个 null，`portrait_source_url` 113 个 null，`portrait_url` 392 个 null，`priority_reason` 391 个 null | 看板置顶/活跃人物卡、30/90 交易时间线、人物页、股票页人物链接及头像、交易证据抽屉。通过 `people.id` 供 `person_id` 关联。页面 `PEOPLE` 映射按 `id` 构建。 |
| `transactions` | `id`、`person_id`、`transaction_date`、`filed_at`、`transaction_type`、`amount_low`、`amount_high`、`asset_name`、`owner`、`verification_status`、`source/source_id`、`source_url`：页面及证据抽屉实际依赖；`ticker`、`ticker_mapping_basis`：可空；`instrument_type`：缺失时显示 `Type not stated`，样本 395 个 null；`option_type`、`strike_price`、`expiration_date`：非期权可空，样本各有 13,229 个 null、4,766 行未提供字段；`disclosure_lag_days`：派生，处理器按 filed date 与 transaction date 计算；`underlying_return_since_trade`、`underlying_return_since_filing`：派生并可空；`performance_as_of_date`、`performance_basis`、`price_source_id`：派生/资格元数据，样本各 7,102 行缺字段（HTML 兼容部分交易无行情）；`performance_eligible`、`performance_ineligible_reason`：派生资格状态，后者样本 10,873 个 null；`filing_id`：审计/文件关联；嵌入 `person`：处理器按 `person_id` 从 `people` 派生 | 看板窗口块、时间线筛选、热门关注标的、共识区、人物页交易表及披露后表现、股票页交易统计/方向/金额/人物链接、交易详情抽屉。`id` 用于打开单笔详情；`person_id` 关联人物；`ticker` 关联证券市场和 ticker 页。 |
| `reported_holdings` | `id`、`person_id`、`report_period_end`、`filed_at`、`asset_name`、`owner`、`value_low`、`value_high`、来源/验证字段：原处理器与规范记录校验依赖；`ticker`、`ticker_mapping_basis`：可空，样本各 4,351 个 null；`change_from_prior`、`instrument_type`：当前 HTML 未直接读取；处理器可从 `asset_type` 派生默认工具类型 | 当前 HTML 已不再呈现人物持仓列表或持仓变化面板。股票页仅在 market 和交易均无公司名时，尝试以 `reported_holdings.asset_name` 作为公司名回退。持仓仍是第五组业务数据所需的 canonical collection，不能由“此页面少用”推出可从快照删除。 |
| `security_market_data` | `ticker`、`source_id`、`price_history[].date/close`：行情图及关联依赖；`current_price`、`as_of_date`：行情摘要依赖，可由序列末点推导；`previous_quarter_end`、`previous_quarter_end_price`、`quarter_change_pct`：季度变化数据，其中没有基准价时可空；`feed`、`timeframe`、`adjustment`、`price_source`、`source_url`：数据来源/口径和审计字段；`company_name`：样本存在，但当前 HTML 查找的是 `market.company`，非此字段（潜在映射差异，详见差异清单）；样本 `previous_quarter_end_price` 和 `quarter_change_pct` 各 4 个 null | 股票页价格历史图、当前 EOD 价格、前季度末变化；看板热门股票价格变化排序/显示。 ticker 精确关联。HTML 无价格行情时显示“无价格数据 / 等待行情”，缺失变化显示 `—`，不应替成 0。 |
| `source_health` | `source_id`、`source_url`、`status`、`last_successful_sync_at` / `data_cutoff_at` / `last_checked_at`：来源区实际选择和显示依赖；`source`：显示名称，缺失时回退 `source_id`；`detail`、`source_type`、`contributes_canonical_rows`：本 HTML 页面当前不读取或只用于其他处理/审计；五条记录不等于五条都会显示 | 页面来源链接区按 `processing.disclosure_source_ids` 过滤，只显示有 `source_url` 的披露来源；取成功时间、来源 cutoff、最后检查时间中可用且最新者；`status=ok` 显示 Synced，否则显示 Delayed。行情及伦理指导健康行不会仅因存在于数组就显示。 |

原处理器把 `people`、`transactions`、`reported_holdings`、`security_market_data` 和 `source_health` 作为五个 canonical arrays；其余摘要、`recent_transactions`、`market_moves` 属处理器构建的派生投影。HTML 的 `market_moves` 直接消费与这一划分一致：它是派生的窗口统计输入，不是第六个 canonical collection。

## 3. 关联键、页面行为与筛选窗口

### 关联键

| 关联 | HTML / 处理器行为 |
|---|---|
| 人物 → 交易/持仓 | `people.id = transactions.person_id = reported_holdings.person_id`。处理器拒绝未知 `person_id`；HTML 以人物稳定 ID 建立映射，不以姓名作主键。 |
| 证券 → 交易/行情/持仓 | `ticker` 字符串作为跨集合关联键；市场数据按 ticker 唯一。交易或持仓 ticker 可空；缺 ticker 记录仍存在于人物/时间线，但不进入 ticker 统计和行情连接。 |
| 单笔证据 | 交易 `id` 用于详情抽屉；`filing_id` 是披露记录的文件关联信息，`source_id/source/source_url` 显示官方来源。HTML 没有独立 filing canonical 数组。 |
| 行情/交易统计 | `market_moves[window].ticker` 连接窗口交易；它携带窗口内首次交易日、回报基准日、行情 as-of 和回报。 |

### 可见功能与筛选

- Dashboard 同时渲染 30 日和 90 日交易块；顶部窗口控制是页面滚动锚点，不会隐藏另一个窗口块。两个块可各自选择热门股票或交易明细；交易明细各自有搜索、方向、instrument 筛选。
- 大多数“近 N 日交易”按 `transaction_date`，区间包含 `data_cutoff_at` 当天，开始日为 cutoff 减 `N-1` 天。样本 cutoff 日为 2026-09-27。
- “Capitol Hill Watch”按 `filed_at`，即近期披露而非近期交易；代码中 30/90 日均为包含截止日的日历日窗口。
- 人物页和股票页 30/90 筛选按交易日期；方向筛选为 all/purchase/sale。人物页对置顶/featured 人物在当前窗口无记录时可显示最近最多 100 条历史记录，并显式标出这是历史回退而非当前窗口总数。
- 股票页的价格图使用 ticker 的行情历史序列；交易事件标记来自该 ticker 的全部交易，不被 30/90 表格窗口筛选限制。价格变化与披露方向分开呈现。
- 点击交易打开官方来源证据抽屉；页面支持人物→股票→返回人物/看板、价格图标注与 hover 提示、Esc 关闭抽屉/子页面。
- HTML 页面确实读取 `market_moves`；它以每个 30/90 日窗口的首次交易日和行情基准计算价格变化，不等价于官员收益或交易收益。

### 缺失值语义

- `ticker=null`：未能映射到证券 ticker；该笔披露仍是真实记录。页面可显示 unmapped ticker/资产名，不纳入 ticker 榜单或行情连接。
- 收益字段 `null`：不可计算/不可用，不是 0%。HTML 的百分比格式器对 null/undefined/空字符串显示 `—`，且不着涨跌颜色。
- 行情或基准价格缺失：显示无行情/等待行情，或者变化 `—`；不可伪装为横盘/零收益。
- 人物可选身份属性缺失：当前页面会过滤空显示字段或使用明确回退；不把空值直接打印为 `null`。portrait 图片字段缺失时影响图像呈现，页面对所有视图并无统一图片 fallback 保证。
- 非期权的 `option_type/strike_price/expiration_date` 为 null 属正常；期权页面事实使用这些条款字段，缺字段会让详情无法闭合，不能猜补。
- `instrument_type` 缺失由页面写作 “Type not stated”；这是字段未提供，与申报本身明示 unspecified 不等价。
- 金额上下限表示申报金额区间，页面分别格式化两端。样本交易金额在所有 18,017 行都有数值；但旧处理器会把缺失金额默认成 0。对于新流水线，若原件金额未知，不得依靠该默认值把未知伪装成零金额，须隔离或采用契约能够准确表示的语义。
- `source_health` 时间可能有多个候选时间字段；HTML 优先 successful sync，其次 source cutoff，最后 check time。不得把一次检查误写成一次成功同步。
- Holdings 表示报告期申报值，不等于实时持仓；HTML 当前没有持仓明细交互。`change_from_prior` 不能单独被解释为确认的买入/卖出或平仓。

## 4. 与旧交接材料的差异清单

| 项目 | 旧交接 / 计划材料 | 当前 HTML 观察 | 处理方式 |
|---|---|---|---|
| 基准优先级 | ZIP 实施计划 §1、§10.3 A 及旧仓库目标文字，称交接包/说明/处理器/渲染器是唯一且最终消费契约，HTML 不得凌驾 | 用户在 2026-09-29 明确以本 HTML 为最新前端实现，冲突时新 HTML 优先；并已在当前 AGENTS/目标基线记载 | **直接冲突。以用户最新指令及 HTML 为准；旧优先级说法不再生效。** |
| 人物页功能 | 旧材料/早期映射记载人物持仓、年度活动、最常交易证券等面板/标签 | 当前人物页为披露后价格表现摘要和披露交易表；旧持仓/年度活动/常交易 ticker 等面板已不在 HTML 页面流中 | 页面消费矩阵按新 HTML；holding canonical 数据仍保留，以当前其他页面或后续 HTML 验收为准。 |
| `recent_transactions` | 旧处理器固定产生该派生数组；旧文档讨论保留完整交接数组 | 新 HTML 内该数组 18,017 行且与 `transactions` 完全相同；脚本未直接读它 | 当前 HTML 观察中是冗余投影；不据此删除处理器输出或声称其他消费者不依赖。 |
| 摘要数组 | 旧处理器生成 summary/activity/priority/top 等派生结果 | HTML 脚本对上述字段无直接访问，自己从 canonical 数据计算部分卡片/汇总 | 当前页面无需从这些投影读取；旧问答/处理器消费者是否依赖须另行确认，不属于本次 HTML 观察可证明范围。 |
| 热门关注/共识实现 | 旧映射可能把相应模块描述为来源预计算摘要 | 当前 HTML 对 canonical 交易和 `market_moves` 在浏览器端计算热门股票、方向及交易窗口统计 | 后端投影只有在相同输入/窗口/排序/空值语义验证后才能优化或替代。 |
| 持仓展示 | 旧数据地图描述 holdings 供人物页及持仓信息模块消费 | 当前 HTML 无人物持仓明细入口；股票页只以 holding 资产名作最后公司名回退 | 页面字段矩阵体现当前消费；不要把“HTML 少用”写成允许删除 holdings 事实。 |
| 公司名称属性 | 处理器/数据字段使用 `company_name` | HTML 当前 ticker 页面读取 `market?.company`，样本行情记录没有 `company`，随后回退交易资产名、holding 资产名或 ticker | 存在潜在映射不一致。是否为有意 fallback 属推断；实现前核对真实页面文字和样本记录，不能未经用户基准修改字段含义。 |
| 30/90 窗口描述 | 旧交接说明部分以通用窗口描述数据接口 | 当前 HTML 显式区分 transaction date 窗口和 filing date 窗口；Dashboard 两个交易块同时出现，顶部按钮是锚点；人物/股票页则是真筛选 | 以 HTML 实际行为为准，不能用单一日期字段替代另一时钟。 |
| 空值/统计 | 旧文档中记录过金额缺失默认 0、空表现出现 0、无行情时错误基准等风险 | 当前 HTML 的收益 null 显示 `—`；人物/股票空窗口写明“无披露”；金额上下界仍依赖输入有可表示的上下界 | 缺失值行为逐字段遵循新 HTML，并保留不能由页面准确表达的金额异常到隔离。 |

## 5. 字段依赖的直接观察与推断边界

### 直接观察

1. 顶层 DATA 的五个业务 canonical 数组、辅助投影、上述记录计数、样本 `meta` 字段以及字段空值计数，直接从 JSON 读取。
2. 页面实际事件和模板行为来自 HTML 脚本中的 `windowTransactions`、`windowDisclosures`、`renderTimeline`、`renderHotStocks`、`renderSources`、`openTransaction`、`renderPersonPage`、`renderStockPage` 等函数及页面事件绑定。
3. 原处理器 `review-input/us-politician-trades-watch/scripts/process_snapshot.py` 将五个数组作为输入；会校验稳定 ID、人物引用、官方来源/来源 ID、生产验证状态、日期、金额范围、行情唯一 ticker、价格序列排序与积极价格值，并派生 disclosure lag、市场回报和多种摘要。
4. `review-input/us-politician-trades-watch/references/data-model.md` 直接把五数组列为 canonical schema，并将 `summary`、`activity_by_day`、`priority_people`、`top_people`、`top_tickers`、`recent_transactions`、`market_moves` 描述为派生投影；HTML 所见 `market_moves` 被页面消费，其余所列投影未被 HTML 直接消费。
5. 当前 HTML 与旧交接脚本/文档的差异仅证明两个具体样本/版本不同；并不自动证明某方 bug 或所有消费者的最终行为。

### 推断（用于实现规划时须验证）

- “页面必需”是从 HTML 解引用/插值和关联流程推断，不等于处理器对每个字段都实现了严格必填校验。例如 `person_id` 由处理器强校验；portrait data URI 在模板中使用但处理器未必作非空硬校验。
- 样本内相同长度与字段集合可用于确认该 HTML 的 `recent_transactions` 数据逐行重复情况；如果未对两个数组规范化比较所有值/次序，就不推广为所有版本一定完全重复。当前审计检查了样本数组长度与字段全集；记录级完全相同描述只适用于样本检查结果。
- HTML 代码对 `company` 的访问和样本的 `company_name` 不一致是可见事实；认定它是 schema 错误、旧属性兼容遗留或有意回退属于推断。
- 当前页面对 canonical holdings 的弱消费，不足以推断后端可以停止构建或发布它；页面外的交接 query、自动测试和目标产品可能另有依赖。
- “未直接访问某字段/数组”不是“可安全删除”的证据。需与处理器测试、query、五数组验收以及现有生产分片/契约回归一起判断。

## 6. 局限与不可据此声称的事项

- 本审计检查的是一份固定 HTML 快照，不等价于从生产 `main` 实时读取的版本，也不覆盖未来 HTML 更新。实施前需再次检查文件与用户当前指定的哈希；若用户给出新文件，以新指定为准。
- 本次没有运行页面浏览器、处理器或测试，也没有验证 HTML 中数据是否与当前远端候选同一时点、同一来源 commit 或同一行数。因此不声称本次端到端验收通过，也不将 ZIP 自述测试数量算成本次测试结果。
- 计数只按内嵌 DATA 原始数组计算。其记录可能有资格状态、数据截止和范围限制；不能把数组数量直接等同全源发现数、覆盖率、生产正式发布数或可交易证券数。
- “未用字段”只针对本 HTML 当前脚本直接读取。其他旧交接 query、冻结 processor/renderer 测试、后端构建和问答消费仍需分别检查，不能由本表单独决定删除字段、数组或兼容代码。
- 本文依据用户规定的优先级解释冲突；ZIP 文档中要求 HTML 不能凌驾旧契约的段落不适用于当前执行基线。
