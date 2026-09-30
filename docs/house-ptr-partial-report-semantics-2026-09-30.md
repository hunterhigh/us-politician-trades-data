# House PTR：保留合格交易并说明报告覆盖缺口

日期：2026-09-30。范围仅为隔离分支上的只读契约审计和审计侧输出，不改变候选、`review`、`main`、工作流或最新 HTML。最高优先级 HTML `politician-disclosures (3).html` 再次核对 SHA-256 为 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。

## 现有契约实际表达什么

| 层 | 实际字段与行为 | 对单份报告覆盖的局限 |
|---|---|---|
| House PTR 资格 | `transactions` 与 `quarantined` 分行；有任意合格交易就给 `qualification.status=qualified_rows`、`production_eligible=true`。 | 这是行级事实资格，不证明整份 PDF 的物理行已全部发现。`9116290` 因而合法保留一条合格行，但另外至少七行没有得到处置。 |
| 规范交易及最新 HTML | 交易含 `filing_id`、官方 `source_url`、`verification_status=official_matched`。最新 HTML 内嵌的 `9116290` 交易仍为 ID `house-ptr:c2a121352254ef9a9983f263`、`official_matched`。 | 没有 `report_coverage_status`；`official_matched` 只适用于该交易行，不能解释成报告完整。当前页面的记录数、买卖数与金额区间均按已出现交易计算，不能显示这份文件的缺失下界。 |
| `source_health` | House 来源级 `status=partial`；最新 HTML 中该条的 `detail` 只说完整日截点保留 3,352/3,352 交易及 1,840/1,840 持仓。页面 `renderSources()` 对所有非 `ok` 状态只显示 `Delayed` 和同步时间，不读取 `detail`。 | 这是整个 House 来源的同步状态，不绑定 `filing_id`，也不能区分“同步延迟”与“某份 PDF 漏行”。往 detail 塞文件清单既不被页面展示，也无法作稳定机器契约。 |
| 生产 manifest | `coverage.scope=all_input_records`、`universe_complete=false`，以及人数与行情覆盖；`source_health` 是来源级数组，另有 `status_revision`。 | `all_input_records` 只覆盖进入构建器的输入记录，不是官方原件的物理行守恒；`universe_complete=false` 也不给出报告 ID。构建器限制 source_health 字段，现有 manifest 没有 per-report 结构。 |

因此现有前端与 manifest **无法同时明确表达**“这条交易已按当前自动规则晋级，可继续展示”和“其 PDF 还漏了其他交易”。直接把行的 `verification_status` 改成隔离会撤下现有候选；把整份报告设为失败并重建候选，也会使 `9116290` 的既有候选消失。保留现有事实时，应另记**报告覆盖状态**，不得把它混同于交易行资格、来源同步健康或快照输入覆盖。

## 最小隔离侧状态原型

`backend/src/unison_snapshot/house_ptr_coverage.py` 增加纯函数 `assess_report_coverage()`，**未接入候选或发布路径**。输入为固定资格文件、完全匹配的原件 SHA-256，及独立逐页图像核对所得的“至少可见交易行数”。输出[两份固定报告的审计侧样本](house-ptr-coverage-sidecar-sample-2026-09-30.json)：

| 报告 | 可见物理行下界 | 存档已处置（合格 + 隔离） | 尚未处置下界 | 状态 | 既有候选 |
|---|---:|---:|---:|---|---|
| `9116290` | 5+6=11 | 1+3=4 | 7 | `known_incomplete` | 保留原 ID `house-ptr:c2a121352254ef9a9983f263` |
| `9115813` | 4+5=9 | 0+1=1 | 8 | `known_incomplete` | 无 |

此计数下界不生成缺失交易，也不把视觉观察转成 `official_matched`。当已处置行数达到或超过观察下界时，函数只返回 `unverified`；**数量相等并不证明每个原件行均被正确处置**。未来的 `verified_complete` 必须取得每个物理行与“合格、隔离、明确排除”记录之间的一对一映射，并核实页数与来源哈希，不能由本函数或 OCR 日期词数推定。

输出随原件 SHA-256 与资格结果绑定，保留已合格行 ID 作审计对账；哈希不符或计数无效会失败关闭。定向测试证明 `9116290` 型 11/4 仍保留候选 ID、输入资格对象不变，等量计数只得 `unverified`，无效证据拒绝。样本取自固定 `evidence` 提交 `5fe5f0adb3181c5d1552dff3f07336464c3043a7` 与 `review` 提交 `a814fde2bb9664c2b97981a32ce86ab9b3daf223`，逐页依据见[扫描报告核对](house-ptr-four-scan-closure-2026-09-30.md)与[页级门禁风险审计](house-ptr-ocr-page-gate-risk-2026-09-30.md)。`9116290` 的两页 5/6 行来自该固定原件的目视计数。

## 后续发布边界

该 sidecar 目前是审计输出，最新 HTML 不会显示它，生产 manifest 也未承诺读取它。若要对用户展示覆盖缺口，应先制定 per-report 规范字段或可寻址状态清单、使前端按 `filing_id` 关联并明确“记录数为已确认部分”；同时定义发布时新旧状态的原子对账与已成功报告的历史候选保留规则。当前既有候选继续保持原 ID 与值；漏行不得自动晋级。只有逐页物理行检测和逐行处置守恒可验证后，才可把 `known_incomplete` 升为 `verified_complete`。

本分支实际运行 `python -m unittest discover -s backend/tests -p 'test_house*.py' -q`：72 项通过；两份固定资格及原件哈希生成 sidecar 样本成功。没有重建正式候选、修改 HTML、启动重试或触发生产发布。
