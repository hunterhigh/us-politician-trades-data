# v1 状态

2026-10-30：v1替代原35项；历史在archive/pre-v1-docs。PR293已启用v1。发布改为各来源独立真实水位，Senate受阻不再单独阻断全部发布，边界如下。

## 有效决定

House/OGE采集及合格水位48小时；Senate宽限30天（`SENATE_MAX_STALE_DAYS`），20天提醒一次，超过停发告警。独立真实截止时间，保留Senate旧事实，前端标明缺口；来源告警保持，正式恢复后关闭发布窗口告警。Senate仅每日正常采集，自动补齐成功以来区间，不做访问诊断。允许告警Issue、内容失败回退；网络仅重试。回填限2025 House PTR。语义见[契约](data-contract.md)。

取消v1.1日期、稳定一周条件及其定时检查；连续推进，不按日期/每日节奏。两种方法未推进同一验收项即记待决、继续独立步骤；仅产品范围、付费或删除生产数据须询问。用户接受src 69,492、tests 59,137行，瘦身结束。

## 已知限制

1. 扫描字符冲突、缺字：行隔离。
2. 复杂边框、贴边笔迹：不能可靠识别则隔离。
3. 手写日期：缺必要日期整报隔离，不推断。
4. 扫描续行、跨页归属：不能唯一关联则隔离。
5. 无漏行证明不足：保持partial。
6. 扫描修订或缺失前件：保旧，隔离新报告。
7. 需申请的OGE 278-T：request_required，v1不申请。
8. 历史ID通用重编号：不做，沿用原ID。
9. Paddle/TrOCR未采用实验：退役，不再运行。
10. 334份OGE旧缓存缺当前完整证明：保留5212条既有事实，保持待处理，不重新解析。

## 当前结果

本轮发布比较基线为main 13918a8c：403人、19357交易、5595持仓；正式发布结果与差异见对应发布PR及[健康入口](https://github.com/hunterhigh/us-politician-trades-data/blob/health/status/production_health.json)。3115行YAML接受，不再压缩。

PR303–306受控原件重跑及候选投影完成：226份361行底纹误判消除，原始交易对象不变；身份恢复15/16，3电子长表修复。合格440、部分6、隔离69（52纸表失败），合格7643行、隔离444行。重试锁定三份电子长表ID+哈希，纸表同错不重跑。受控[收据](https://github.com/hunterhigh/us-politician-trades-data/blob/review/status/house_backfill_2025.json)；集成/守恒证据维护于[PR303](https://github.com/hunterhigh/us-politician-trades-data/pull/303)。

人物缺口、清理、改名及冻结链路演练已验收（PR296/299/300）。PR303–306负例及2026候选字节守恒通过。新发布规则与正式快照差异见[PR307](https://github.com/hunterhigh/us-politician-trades-data/pull/307)。

## 待决事项

Manning 20026638身份：已试官方名册、Clerk个人页，缺可重放历史身份原件；8行继续隔离，建议取得118届官方名册后再绑定，不推断；20029046另有未解修订关系。
