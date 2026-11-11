# v1 状态

2026-11-11：v1已替代35项，由PR293启用；旧文档在archive/pre-v1-docs。

## 有效决定

House/OGE水位48小时；Senate宽限30天（`SENATE_MAX_STALE_DAYS`），20天提醒，超过停发。各来源独立真实截止，保留Senate旧事实并展示缺口；来源告警保持，正式恢复后关闭发布窗口告警。Senate采用人工下载、自动导入；长期收件分支senate-inbox及每周一01:00 UTC提醒Issue已启用，日常仅需浏览器保存和GitHub网页上传。允许告警Issue、内容失败回退；网络仅重试。回填限2025 House PTR及归档年报持仓；五来源共用版本重跑，不以旧版替代当前解析。新记录无唯一代码时ticker为空并显示未匹配；历史ticker绑定独立于当前行情资格。见[契约](data-contract.md)。

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

House重跑已完成：[最新运行](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/42978416764)待解析0、904份资格产物；2026 PTR均完成当前对应解析版本处理。合格水位已推进至2026-11-11 08:08:48 UTC，与本次采集一致。[正式发布](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/43020551928)已成功，main从eed9231c推进至719a5eac7。

[PR348](https://github.com/hunterhigh/us-politician-trades-data/pull/348)移除旧变化证明门槛，改为每次main随附新增、修改、删除及字段前后值清单；仅数据合同不符、任一来源记录数一次减少超过5%、来源过期时停发。OGE旧值回填已由PR349修正，[重建运行](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/42989506850)完成，待解析0、保留旧版0。直接OGE交易5693→5239；含白宫的OGE来源14155→13701；变化已随main发布清单。

按用户追加要求，[PR350](https://github.com/hunterhigh/us-politician-trades-data/pull/350)移除House候选、状态统计的批量原件重放，复用已有资格产物；首次资格判定和现有修订流程保留。完整CI已通过，源码净减98行、测试未增。正式快照生成用时43分43秒，前两次为84、108分钟。


Senate由[PR351](https://github.com/hunterhigh/us-politician-trades-data/pull/351)、[PR353](https://github.com/hunterhigh/us-politician-trades-data/pull/353)、[PR354](https://github.com/hunterhigh/us-politician-trades-data/pull/354)接入人工收件、按报告编号去重、纸表现有OCR入口及自动发布交接。[实际导入](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/43040416981)已自动完成1份既有电子PTR保存件、1笔合格交易及组装；原件标注manual_browser，inbox清空，Senate候选水位2026-11-11 14:40:13 UTC。导入数、重复及错误反馈统一见[提醒Issue352](https://github.com/hunterhigh/us-politician-trades-data/issues/352)；正式快照经[既有发布流程](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/43040781632)自动推进，当前正式结果以[main manifest](https://github.com/hunterhigh/us-politician-trades-data/blob/main/manifest.json)为准。维护者步骤见operations。

## 待决事项


Manning 20026638身份：已试官方名册、Clerk个人页，缺可重放历史身份原件；8行继续隔离，建议取得118届官方名册后再绑定，不推断；20029046另有未解修订关系。
