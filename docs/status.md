# v1 状态

2026-10-29：v1替代原35项交付，历史见archive/pre-v1-docs。PR293已获用户确认并合入code（d796df52）；Senate受阻，全站继续停发，正式main未移动。

## 范围与授权

Senate停止访问诊断，每日一次正常采集发现恢复。允许告警Issue、内容失败自动回退；网络仅重试。只回填固定2025 House PTR，不扩年份、年报或供应商。字段及支持范围见[data-contract.md](data-contract.md)。

最新安排：取消v1.1日期和稳定一周条件，相关定时检查已删除。本轮连续完成：PR296完整CI通过并合入、2025回填解析/资格判断→人物级可靠绑定未解析计数→代码瘦身（backend/src<50k，测试同比缩减）→code冻结输入全链路、冒烟与回退演练。不按日期或每日节奏推进，中途无需逐步汇报。

同一问题连续两种方法均未推进验收：在待决事项记问题、方法、判断与建议，继续独立步骤；仅产品范围、付费资源、删除生产数据须询问。最后一次汇报不超过10行。

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

## 当前结果与下一步

本机成果已备份；初始候选v1/candidate-20261028、最终候选v1/rc-20261029-a3082fdc固定。[PR293](https://github.com/hunterhigh/us-politician-trades-data/pull/293)完整CI及整合演练通过：403人、19357交易、5595持仓，公开读取/渲染正常。

[健康入口](https://github.com/hunterhigh/us-politician-trades-data/blob/health/status/production_health.json)切换后检查通过。10条工作流、3115行YAML接受，不再压缩。[PR296](https://github.com/hunterhigh/us-politician-trades-data/pull/296)删除9旧文档、闲置模块和12命令注册，CHANGELOG移根目录；冻结候选、截点及状态逐字节相等，完整CI修正测试夹具中。

2025回填[40810521752](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/40810521752)：515原件已归档，归档队列0。首批解析完成，资格阶段被非2025旧缓存版本拦截，正在修正固定年份的处理边界。验收：2025待处理队列0、每份报告有状态及原因、旧正式事实守恒。

## 待决事项

无；剩余工作继续执行。
