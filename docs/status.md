# v1 状态

2026-11-10：v1已替代35项，由PR293启用；旧文档在archive/pre-v1-docs。

## 有效决定

House/OGE水位48小时；Senate宽限30天（`SENATE_MAX_STALE_DAYS`），20天提醒，超过停发。各来源独立真实截止，保留Senate旧事实并展示缺口；来源告警保持，正式恢复后关闭发布窗口告警。Senate仅每日正常采集、自动补缺，不诊断访问。允许告警Issue、内容失败回退；网络仅重试。回填限2025 House PTR及归档年报持仓；五来源共用版本重跑，不以旧版替代当前解析。见[契约](data-contract.md)。

v1.1日期、稳定一周及定时检查已取消；本轮连续完成。两种方法未推进同一验收项即记待决、继续独立步骤；仅产品范围、付费或删除生产数据须询问。用户接受src 69,492、tests 59,137行，瘦身结束。

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

## 当前结果

正式main仍为4010b3f3：584人、27228交易、19499持仓，OGE直接来源5693笔。[PR331](https://github.com/hunterhigh/us-politician-trades-data/pull/331)/PR332共用重跑已合入、完整CI通过。OGE 339份当前解析已保存（319份新版、20份当前校验），解析失败0、采集待解析0；新版资格候选5693笔，尚未完成整合发布，review待解析未清零。3115行YAML接受，不再压缩；[健康入口](https://github.com/hunterhigh/us-politician-trades-data/blob/health/status/production_health.json)。

人物缺口、清理及冻结链路已验收（PR296/299/300）；House回填、负例及2026守恒见[PR303](https://github.com/hunterhigh/us-politician-trades-data/pull/303)。三份长表按ID+哈希重试，纸表同错不重跑；发布切换证据见PR307。

## 待决事项

OGE整合：完整重跑、显示名称兼容重试后，ARI仍无唯一活跃SIP匹配（oge-278t:15141742f9a9d3d335e4cb1e）；review待解析315份、保留旧版4696笔。判断是历史ticker绑定耦合当前行情资格，建议分离后再整合，不猜继任证券、不改旧事实。按两方法规则停止；撤掉提前检查，恢复正常采集。[失败证据](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/42602307345)；原件及新版解析已保存。

Manning 20026638身份：已试官方名册、Clerk个人页，缺可重放历史身份原件；8行继续隔离，建议取得118届官方名册后再绑定，不推断；20029046另有未解修订关系。
