# 最新 HTML 端到端契约差异审计（2026-09-29）

## 固定基准与覆盖范围

目标 HTML 固定为用户指定的 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256=`D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。该 HTML 是冲突时的最高优先级；`review-input/` 的冻结处理器和 renderer 仅作历史兼容对照，不能降低 HTML 基准。

本审计把 HTML 内嵌 DATA 交给冻结 processor 做内存诊断对照，并逐行比对页面直接使用的派生字段；没有改动 HTML、生产 builder、renderer 或发布数据。

## 可复现对照结果

- `backend/tests/test_producer.py`：19/19 通过。
- 冻结 processor `review-input/us-politician-trades-watch/tests/test_process_snapshot.py`：29/29 通过。
- 这 48 项测试由并行契约审计实际运行，分别验证生产 builder 和旧冻结 processor；它们不构成最新 HTML 浏览器端到端验收。
- 原始 HTML DATA 的 `source_health` 包含 `twelve_data_split_adjusted_eod`，但旧 processor 仅允许 House/OGE/Senate 披露来源，直接输入会拒绝该来源健康记录。HTML 的 `processing.disclosure_source_ids` 仅列 House/OGE/Senate；行情数组的 1,748 项全部来自 `alpaca_sip_eod`，Twelve Data 记录仅作为被排除 ticker 的健康/覆盖说明。诊断副本移除这条不属于页面披露来源白名单的健康记录后，旧 processor 成功处理五个 canonical arrays；其 `market_moves` 的 30 日 44 条和 90 日 539 条与 HTML 投影逐项一致。
- 集成 builder 虽将 Twelve Data 加入合法 `source_health` ID，但目前只按行情数组是否包含 Twelve Data 行来选择 v2 processor。最新 HTML 这类“只有 Twelve Data 健康行、无 Twelve Data 行情”的数据仍会选 v1，v1 随后拒绝该健康行。需修正 processor 选择条件并给 builder 增加回归输入，才能接收 HTML 当前合法的数据形态。
- 初次以 HTML 内嵌的 252 日展示窗口复算时，4,776 个 `underlying_return_since_trade` 与 1,284 个 `underlying_return_since_filing` 有差异。这些差异来自展示窗口早于事件日的历史价格被裁掉，不能据此断言原 HTML 的收益错误。
- 固定 `main=958b173ca37964dfa60e4762c6ef2931e3ed06ea` 引用 `market=29251969fbadb06565781aa9c9d5bafd3ce6e00e` 的 50 个行情页。新增只读审计 `backend/scripts/audit_latest_html_market_returns.py` 核对每页内容 SHA-256，找到 2,477 条完整行情；HTML 接纳的 1,748 条行情所展示的窗口全部等于完整序列的末段。36,034 个事件收益字段中，21,752 个数值复算一致、14,275 个同为空值，剩下 7 个 HTML 有值但事件早于该 ticker 的完整行情首日。审计的退出状态为 1，明确表示这 7 项未闭合。
- 以 MSFT 为例，HTML 的展示序列覆盖 2025-09-25 至 2026-09-25（252 个交易日），但交易日为 2025-04-22；固定完整行情中的事件日收盘价为 366.82，最新价为 516.17，复算为 `+40.71%`，与 HTML 一致。旧 processor 仅使用裁剪后的窗口，得到 `+1.80%`，其 `price_at_or_after()` 未检查目标日期是否在行情覆盖区间内。
- 未闭合 7 项涉及 TEVA 2 项、FSSL 1 项、AZN 4 项；对应完整序列分别始于 2026-09-14、2025-11-13、2026-02-02，均晚于交易或申报事件。HTML 数值逐项等于以首日行情作基准的回退结果，不能称为事件日至今的可验证收益。
- `filed_at=2026-08-20` 的样例在行情序列覆盖区间内，可按该 processor 规则复算并与 HTML 相等。`2026-09-24` 的申报事件早于 `2026-09-25` 的序列首日，processor 又错误采用首日价格。

HTML 的绝大多数早期事件收益可由所引用 `market` 提交的完整历史序列复算；仍有 7 项因完整序列也缺事件日价格而不可验证。HTML 是前端字段与交互的最高优先级基准，同时这 7 个数值需要保留明确的数据质量例外。

## 浏览器验收状态和下一步门槛

现有 `frontend-e2e/tests/production.e2e.mjs` 依赖旧包装标记（`OFFICIAL DISCLOSURE DATA`、`IS_DEMO` 和 `meta.is_demo=false`），最新 HTML 不使用这套包装。现已增加 `frontend-e2e/tests/latest-html.e2e.mjs`，先核对用户 HTML 哈希，再通过本机 Chrome 的 DevTools Protocol 实际载入原文件；整合分支运行返回 `passed`。验证范围包括两个 dashboard 窗口、30/90 日 tab 与搜索隔离、90 日锚点、交易详情 drawer、人物和证券页导航，以及一条同时缺 ticker/工具类型/交易日收益的真实记录在页面以破折号和说明文本显示。浏览器观察到 1,748 条行情、7,131 个缺交易日收益、6,297 个缺 ticker、395 个缺工具类型。未遍历全部人物和空值组合，也未测试托管页面或其他浏览器/视口。

不得把序列首日冒充事件日行情来消除剩余 7 项差异。浏览器主要交互已在用户 HTML 原文件上验证；7 项若要继续显示事件收益，须有许可且可校验的事件日行情。缺少时应标示不可计算。

## 结论边界

K 阶段的固定行情来源复算已把收益差异缩小到 7 项，最新 HTML 的本地 Chrome 主要交互也已通过；仍须处理这 7 个无法证明的数值，并在需要时验证托管版本，才可宣称整体端到端通过。审计不改变生产数据或 HTML。
