# Senate 首份纸面 PTR：36 行身份与前端字段门禁

固定报告 `068274e1-4b7a-4453-a242-563dde4c10d8`。本次只读核查
官方目录、归档 HTML、封面与表格页、Senate 名册、review 身份绑定、
修订补充清单，以及用户指定的最新前端 HTML。所有源及逐行门禁见
[机器审计](senate-paper-first-report-frontend-gates.json)；该文件逐行引用
[已确认四字段证据](senate-paper-first-report-fields.json)的固定哈希，
不生成或晋级交易候选。

## 报告级可核事实

- 官方目录中的此件是 `/search/view/paper/`，列示日期为
  `2026-02-12`；归档原件 SHA-256 为
  `107c79b1276ae99ef9321547420e2303790fcc3cb110f43bf64e4c3ddb34bb6c`。
  封面函写 Senator Richard Blumenthal、2026 年 2 月 12 日收件，
  且描述交易日期为 1 月 8 日至 1 月 28 日。表格页抬头也写
  Richard Blumenthal。审计保存这两个原图区域的裁剪坐标与哈希。
- 固定 review 身份批次将此件唯一绑定至 `senate:B001277`，
  固定官方 Senate 名册中唯一 `B001277` 为 Richard Blumenthal。
  最新 HTML 的 `people` 中也有该人物。人物身份可作为
  **报告级观察**，并未单凭此将 36 行升级为合格交易。
- 表格页的 Amendment 方框内未检出标记；归档元数据的
  `report_amendment_number` 为空，当前已验证的电子修订补充清单
  未把此件列作目标。固定 131 份目录中同一人物共有 9 份报告，
  全部为纸面 PTR、没有电子 PTR。上述事实没有证明 9 份纸面之间
  不存在重报或内容重合，逐报告关系仍记为 `unresolved`。
- 最新 HTML 中该人物现有 1 条交易，来自另一份纸面报告；
  本件当前为 0 条。这个前端状态只作消费端对照，不替代官方原件。

## 逐行字段门禁

36 行的方向、金额档、日期及资产格原文已通过原图复核；
全部资产格以字面 `(S)` 开头。审计保存 `(S)` 为观察值，
同时保留去掉代码后的文字片段供定位，**没有把 `(S)` 自动认作
标准 `owner`，也没有将片段直接写成正式 `asset_name`**。
当前固定原件未提供可绑定的代码释义；旧解析器的约定不能代替
该报告的字段证据。

对照最新 HTML 的交易对象，36 行均缺下列可发布字段：

| 缺失字段 | 当前状态 |
| --- | --- |
| `id` | 未做稳定交易 ID 与跨报告重复关系判定 |
| `owner` | 仅观察到资产格前缀 `(S)`，标准持有人未闭合 |
| `asset_name` | 资产格文字已确认；去前缀后的规范资产名未按持有人规则核准 |
| `filed_at` | 可核 2026-02-12 日期，原件没有精确时间；不伪造时间戳 |
| `verification_status` | 未运行正式候选资格与发布验证 |

`instrument_type` 和 `ticker` 也没有可核定值，单独记为未验证的
可空字段。已确认的 `transaction_type`、`transaction_date` 和金额
上下界只保留在隔离预览中。即使五个缺字段日后闭合，跨纸面报告
的修订与内容等价性仍需独立判断。36 行的最终状态一律为
`quarantined_no_candidate`，`candidate_transaction_id = null`。

审计实际重放命令：

```powershell
$env:PYTHONPATH='backend/src'
python backend/scripts/audit_senate_paper_first_report_gates.py --repo . --fields docs/senate-paper-first-report-fields.json --frontend 'C:\Users\admin\Downloads\politician-disclosures (3).html' --output docs/senate-paper-first-report-frontend-gates.json
python -m unittest discover -s backend/tests -p 'test_senate_paper*.py' -v
```

机器审计 SHA-256 为
`fc161b939a1b9424dcc01f2b995b79161f820a64c2e792cadb06a982eae86c43`。
本机实际重放和相关 18 项测试通过。最新前端基准 SHA-256 为
`D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。
没有修改前端、`review-input/`、正式候选或生产分支。
