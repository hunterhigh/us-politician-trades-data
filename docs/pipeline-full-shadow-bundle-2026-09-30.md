# 三源整源影子账本与正式候选对照

运行日期：2026-09-30。前端实现基准仍是用户指定的 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。用户决定 7 个早于可验证行情首日的收益值为不可计算，页面显示“—”。本审计没有修改该 HTML、`review-input/`、正式候选、`main` 或 `market`。

## 固定运行

三份来源产物和[远端 bundle run 36678478214](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36678478214)均使用同一 `code=68a977e12537bd3235f3c0a8ed765ecd0155adb0`。bundle run ID 为 `pipeline-shadow-36678478214-1`；下载的 `manifest.json` SHA-256 为 `91482b0412e4bf89faa4418b0c781a1f433b52fcb5a86bcc597cd14fe99f644b`，`candidate_rows.json` SHA-256 为 `d68dc27c8ec5ecd92b8ae7a997dba33f12020b1932ddf70740722cfd9588b2e9`。本机只读审计复验了 manifest 声明的 4 个输出哈希、每条账本行的 schema/文档绑定和全部 20,055 行守恒。

| 来源影子产物 | 固定输入 | 文档终态 | 已观察行处置 |
|---|---|---|---|
| [House 36677928549](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36677928549) | `evidence=0bfde57073838652d58ed1fa814b42c49bf7bd5c`，`review=edd5f8081c3c068dbfc1350179cd00a21d5441a7` | 403 份：378 parsed、21 failed、4 no_rows | 4,868＝3,352 qualified＋1,516 quarantined；行文件 SHA-256 `59f9222108239eafc70e7f2c47903500ae7dff499b93ccd458c972dbb8631795` |
| [Senate 36677928697](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36677928697) | `evidence=80f086267677b8fde34314f24024bdc505d97cb0`，`review=9749718e5ae837057db7c031d431714fc72988d1` | 122 份电子 PTR：122 parsed | 1,647＝1,435 qualified＋171 quarantined＋41 excluded；行文件 SHA-256 `e956e50cf79e41e92eb63505013f519d3dce9f9515fe1084dad230618f20a1cd` |
| [OGE 固定重放 36677927511](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36677927511) | 上次成功来源 artifact `36675191583`，`evidence=6d848c5b6787cbe1f206a0f9de2b3679d2d03d4d`，`review=105333d651bb88822ddc6d2565c77d52d49aa5ef` | 329 份 278‑T：318 parsed、11 no_rows | 13,540＝5,040 qualified＋7,490 quarantined＋86 excluded＋924 unrecognized；行文件 SHA-256 `0f1bd757d47656dd7fa35228349ca713a04ef1b09ce2089284c9cb941e9bf9c2` |

合并为 **854 份文档**：818 parsed、21 failed、15 no_rows；20,055 已观察行＝9,827 qualified＋9,177 quarantined＋127 excluded＋924 unrecognized。House 失败文件的物理行数仍为 unknown，不将其计成零。OGE 先前实时目录请求在 [36676892623](https://github.com/hunterhigh/us-politician-trades-data/actions/runs/36676892623) 返回 HTTP 400；本次 OGE 通过 [PR #101](https://github.com/hunterhigh/us-politician-trades-data/pull/101) 的只读入口对上次成功的不可变来源输入、三项输入文件哈希及原件提交重新验证，不能当作实时目录已恢复。

## 与固定正式候选对照

本机以固定 `review` 树中的 Git blob ID 获取并复验 House、Senate、OGE 来源候选，再调用只读审计器。House 3,352/3,352、Senate 电子 1,435/1,435 均以正式交易 ID、官方文档和 URL 直接匹配，共同字段差异为 0。Senate 来源候选总数 1,436，多出的 1 条属于纸面报告，不在电子影子范围。

OGE 5,040 条合格观察值以固定抽取文件逐 blob 核验：309 份抽取文件可把 5,040/5,040 行按官方文档、原件 SHA、页码和印刷行号唯一绑定到旧 `extraction_id`；规范事实字段差异为 0。其中 4,955 条与固定正式来源候选按该 ID、申报文档和 URL 匹配，85 条没有正式候选。旧的纯值比较中 854 条重复值歧义因此得到行身份，但没有为缺失 85 条创造候选。固定资格文件逐条解释这 85 条：77 条为人物身份键冲突，8 条为两个不同官方文档共用原件字节及抽取 ID；没有可靠的修订或合并依据。全 bundle 的 9,827 条 qualified 只有 9,819 个全局唯一 `candidate_id`，8 对跨文档 OGE ID 冲突仍在；该 ID 不能直接充当正式交易 ID。

## 尚未达到切流门槛

1. House 403 份账本仅覆盖存档解析器已发现的行；21 份失败文件物理行数未知。`9116290`、`9115813` 虽标为 parsed，原件视觉清点显示仍有漏行。现有合格事实不能因新门禁而被静默撤下。
2. Senate 当前 catalog 是 131 份，其中 9 份纸面不属于电子影子。4 份纸面旧抽取共 9 行，5 份仍只有 viewer、物理行数未知；第一份的 36 个交易样行仍需资产、日期与身份逐行核准。
3. OGE 278e 年报 Part 6/Part 7、来源版本关系与跨 278‑T 去重仍未闭合；OGE 278‑T 的 85 条缺失资格和 8 对跨文档行 ID 冲突保持隔离。
4. vNext 账本是带证据的观察值，不含足以直接生成前端正式交易的人物、申报、修订和重复决议。`projection_ready=false`。本次未生成五数组候选、未对最新 HTML 运行新候选端到端验收，也未将 vNext 接入正式发布路径。

下一步在固定 source/review 证据上补 House 漏行与 Senate 纸面逐行处置，保留 OGE 年报和身份冲突隔离；为每条可投影观察值建立唯一的正式人物、申报、修订与交易 ID 绑定，再做新旧候选全字段差异和最新 HTML 验收。只有差异和覆盖门禁闭合后，才能由唯一正式发布工作流切换 `main`。
