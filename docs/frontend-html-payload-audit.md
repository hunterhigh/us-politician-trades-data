# 最新前端 HTML 样本载荷审计

审计日期：2026-09-29
范围：只读统计用户指定 HTML 内嵌 DATA 的记录数量、紧凑序列化字节、重复数据和页面直接读取位置；结合 `frontend-html-contract-audit.md` 字段矩阵列出仅限此 HTML 视图的保守候选。本文不是删除授权，也不据此删减规范事实。

## 样本身份与复现口径

- 样本：`C:\Users\admin\Downloads\politician-disclosures (3).html`
- SHA-256：`D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`，与项目指定哈希一致。
- 文件大小：68,817,061 bytes。
- `const DATA =`：1 处，位于 HTML 第 581 行；内嵌 JSON 为 68,587,472 UTF-8 bytes。
- 数组字节口径：解析顶层 JSON 数组，以紧凑 JSON 序列化（`,` 与 `:` 分隔符、保留 Unicode），再计算 UTF-8 字节。不含 HTML 外壳或变量赋值。记录数为顶层数组长度；字段实例数是数组中对象键值对总数；不同字段数为字段名并集。
- 数组完全相等：比较解析后值和顺序，不以字节片段搜索代替 JSON 值比较。
- 本结果只描述此 HTML 快照，不表示运行时内存、压缩传输大小或其他消费者依赖。

复现命令（PowerShell，Python 3；哈希和计数分别核对）：

```powershell
Get-FileHash -Algorithm SHA256 'C:\Users\admin\Downloads\politician-disclosures (3).html'
python -c "import json,pathlib; p=pathlib.Path(r'C:\Users\admin\Downloads\politician-disclosures (3).html'); s=p.read_text(encoding='utf-8'); i=s.index('const DATA = ')+len('const DATA = '); d,_=json.JSONDecoder().raw_decode(s[i:]); [(print(k,'records=',len(v),'compact_bytes=',len(json.dumps(v,separators=(',',':'),ensure_ascii=False).encode('utf-8')),'field_instances=',sum(map(len,[x for x in v if isinstance(x,dict)])),'distinct_fields=',len(set().union(*(x.keys() for x in v if isinstance(x,dict))) if any(isinstance(x,dict) for x in v) else set()))) for k,v in d.items() if isinstance(v,list)]; print('transactions == recent_transactions:',d['transactions']==d['recent_transactions'])"
```

## 顶层数组清单

| 顶层数组 | 记录数 | 紧凑 JSON UTF-8 bytes | 字段实例数 | 不同字段数 |
|---|---:|---:|---:|---:|
| `people` | 392 | 9,392,531 | 5,767 | 15 |
| `transactions` | 18,017 | 19,094,691 | 486,889 | 29 |
| `recent_transactions` | 18,017 | 19,094,691 | 486,889 | 29 |
| `reported_holdings` | 5,593 | 4,226,303 | 100,674 | 18 |
| `security_market_data` | 1,748 | 16,566,381 | 24,472 | 14 |
| `source_health` | 5 | 2,462 | 50 | 10 |
| `activity_by_day` | 440 | 17,379 | 880 | 2 |
| `priority_people` | 1 | 5,327 | 10 | 10 |
| `top_people` | 6 | 783 | 42 | 7 |
| `top_tickers` | 44 | 6,432 | 264 | 6 |

其余根级字段：`meta`、`summary`、`market_moves`、`processing`。它们不是顶层数组；`market_moves` 是对象，按窗口保存记录（A 矩阵记录 30 日 44 条、90 日 539 条），HTML 直接读取。

## 重复值观察

- `transactions` 与 `recent_transactions` 在此样本中解析后逐项、同序完全相等，各自紧凑 JSON 为 19,094,691 bytes。重复数组造成的载荷重复量按一份数组计为 19,094,691 bytes（不计周围 JSON 键与逗号）。HTML 脚本没有直接读取 `DATA.recent_transactions`。
- `people` 的 `portrait_data_uri` 共 392 个值，387 个唯一值；重复多出的 5 项分布于 4 个重复值组。URI 字符数合计 9,241,422，唯一 URI 字符数合计 9,238,872；此处是字符数，不是 base64 解码图像大小。
- `people.id` 在 392 条记录中全唯一；`transactions.id` 在 18,017 条记录中全唯一。
- 五个 canonical collection 及字段职责和空值语义见同目录 `frontend-html-contract-audit.md` 的字段矩阵。本载荷统计不重新推断字段业务含义。

## HTML 脚本直接读取位置

下列行号对应经哈希确认的原始 HTML，读取点按 `DATA.<顶层字段>` 扫描；同一行多次读取合并列示。

| 字段 | 直接读取行 | 页面用途（按 HTML/A 矩阵） |
|---|---|---|
| `meta` | 639、707、721、1612、1980 | 默认窗口、窗口截止日、季度计算、页面 cutoff 和时区显示 |
| `people` | 582、1040、1088 | 建立 `id` 映射、精确规范化名称匹配、稳定排序 |
| `transactions` | 711、725、1423、1458、1598 | 日期窗口过滤、详情按 ID 查询、人物页及 ticker 页交易过滤 |
| `market_moves` | 1326、1735 | 读取 30/90 日窗口派生市场变化统计 |
| `processing` | 1394 | 读取 `disclosure_source_ids` 来源白名单 |
| `source_health` | 1394、1396 | 按白名单和 URL 筛选来源状态并渲染健康信息 |
| `security_market_data` | 1347、1600 | 按 ticker 读取行情快照/历史数据 |
| `reported_holdings` | 1601 | 市场记录与交易都未提供公司名时，按 ticker 回退取资产名 |

A 字段矩阵还指出：`summary`、`activity_by_day`、`priority_people`、`top_people`、`top_tickers`、`recent_transactions` 未被此 HTML 脚本直接读取。该结论仅限定在此 HTML，不代表 processor、查询、测试或其他消费者不需要这些内容。

## 视图优化候选（不构成删减决定）

依赖 A 矩阵后，仅提出针对此 HTML 视图载荷的保守候选。未完成原 HTML、渲染器、处理器及页面兼容验证的字段和数组均标为“候选，不删除”。规范记录与规范事实保持完整。

| 候选 | 本 HTML 观察 | 保守处理边界 |
|---|---|---|
| `recent_transactions` 投影 | 与 `transactions` 完全重复；本 HTML 不直接读；额外重复载荷 19,094,691 bytes | **候选，不删除。** 可评估生成 HTML 专用视图时是否省略该投影；先验证原 HTML、渲染器、processor/schema 消费、交接兼容及既有测试。不可因此删除 canonical `transactions` 或其事实字段。 |
| `activity_by_day`、`priority_people`、`top_people`、`top_tickers` | 本 HTML 没有直接读取点；部分卡片/汇总由页面从 canonical 集合计算 | **候选，不删除。** 仅评估是否从此 HTML 专用快照省略；不得据此停止规范生成或移除其他消费者可能依赖的投影。 |
| `summary` 根级对象 | 此 HTML 不直接读取 | **候选，不删除。** 需做跨消费者及兼容验证，不能推导为规范数据无用。 |
| `people.portrait_data_uri` 重复值 | 392 个值、387 个唯一值；内嵌图片占据大量字符 | **候选，不删除。** 可评估视图侧 URI 共享/缓存或渲染载荷引用方案；须验证离线 HTML 图片、占位图、序列化及页面呈现。不能移除人物身份或头像来源事实。 |
| 五个 canonical collection 的字段 | A 矩阵区分页面依赖、可空/有降级、派生、仅审计/未消费和推断必需字段 | **候选，不删除。** 即使字段未被此页面直接读取或可空，也不得在本预审中删字段；任何视图裁剪须按 A 矩阵逐字段验证兼容，并保留规范记录的审计、来源和原始事实。 |

本表没有提出 canonical collection 删除建议。特别是 HTML 对 `reported_holdings` 仅有公司名回退读取，不改变其规范事实地位。

## 边界

本审计未运行浏览器、原 HTML、渲染器、处理器或测试；未核验远端生产样本与此固定文件同版本。载荷记录数不能直接解释为全源发现量、覆盖率或生产正式发布数量。所有候选须通过 A 矩阵和实际页面兼容验证后才可形成实施决策；本文本身不授权删除字段、数组或规范事实。
