# 三源影子账本与既有交易 ID 的固定绑定

审计日期：2026-09-30。前端最高优先级基准是用户提供的 `politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。7 个早于可验证行情首日的收益值应为不可计算，页面显示“—”。本项工作没有更改该 HTML 或正式发布数据。

输入为[三源同版整源账本](pipeline-full-shadow-bundle-2026-09-30.md)：bundle run `36678478214`，账本代码提交 `68a977e12537bd3235f3c0a8ed765ecd0155adb0`，`candidate_rows.json` SHA-256 `d68dc27c8ec5ecd92b8ae7a997dba33f12020b1932ddf70740722cfd9588b2e9`。固定正式来源候选取自 House `review=edd5f8081c3c068dbfc1350179cd00a21d5441a7`、Senate `review=9749718e5ae837057db7c031d431714fc72988d1`、OGE `review=105333d651bb88822ddc6d2565c77d52d49aa5ef`。本机下载候选时核对了固定 review 树中的 Git blob，绑定器还复算候选文件的 blob ID。OGE 另按固定 review 树逐份核对 309 个旧抽取文件、资格记录与目录记录。

只读绑定结果：9,827 条合格影子观察值中，**9,742 条**按官方文档、原件、行身份和规范字段与既有正式交易 ID 一一对应：House 3,352，Senate 电子 1,435，OGE 4,955。余下 **85 条 OGE** 有明确的固定隔离依据：77 条人物身份歧义，8 条跨官方文档共用抽取 ID。没有补造 ID、将隔离行改成正式事实或把 `candidate_id` 直接用作跨来源交易 ID。结果仍是 `projection_ready=false`。

只读输出保存在本机临时审计文件 `whitehouse-full-binding-map-36678478214.json`，SHA-256 `39CB1B58DE57F8B65639DB4594F72C5A286C405E9BEE31B1F72BFB281324236F`。该输出由当前绑定器对上述历史固定产物生成；**账本的代码 SHA 仍是历史运行的 SHA**，不能把本次绑定器实现冒充为当时已运行的 bundle 代码。

这个结果只证明已观察的合格行与旧正式候选之间的对应关系。House 失败文件和已知漏行、Senate 九份纸面报告的逐行事实、OGE 年报及来源修订关系仍未闭合；也还没有从影子账本构建新的完整五数组候选、对最新 HTML 做新候选端到端验收或切换唯一正式发布工作流。`main`、`market` 与既有正式候选均未因本审计移动。
