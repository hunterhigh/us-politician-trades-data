# House PTR 固定失败样本审计（2026-09-29）

## 基准与范围

本次执行计划 D 对应 House PTR 离线解析。消费目标以用户明确指定的最新 HTML 为最高优先级：`politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。任何更早交接材料只作对照。

固定失败记录来自 `review` 提交 `9749718e5ae837057db7c031d431714fc72988d1`；匹配的只读官方原件来自 `evidence` 提交 `80f086267677b8fde34314f24024bdc505d97cb0`。每份 PDF 的字节哈希都与其归档元数据 SHA-256 相符。样本清单见 [`corpus.json`](../backend/tests/fixtures/house_ptr_failures_2026/corpus.json)。本审计未修改这两个分支，也未访问生产写入口。

## 样本盘点

20 份失败样本共 32 页、699,891 字节，PDF 文本层为 0 页。旧解析器错误中，18 份报告为 `House legacy PTR contains no recognized transaction rows`，2 份为 `House PTR header does not match its archived identity`。页面尺寸方向统计为 15 份横向、5 份纵向；总页数和每份页尺寸固定记录在 JSON 清单。

| document_id | PDF SHA-256 | 页 | 字节 | 文本层页 | 旧重试次数 | 上次失败摘要 |
|---|---|---:|---:|---:|---:|---|
| `8221321` | `a11a8608a49ddd4dc599c5c7cdd00c08d4a18849bd5ca9157f5678f8de83e67b` | 2 | 45,954 | 0 | 10 | House legacy PTR contains no recognized transaction rows |
| `9115704` | `c830ee046083e8894f0a360f011186e755f3bbf0d6b83c7084611e80e1776d81` | 2 | 44,149 | 0 | 9 | House legacy PTR contains no recognized transaction rows |
| `9115711` | `42d8eda1c952b16aa21f1293a8f7f34592441c0757eac9a1650bbc8c95d7e564` | 1 | 22,804 | 0 | 9 | House PTR header does not match its archived identity |
| `9115808` | `05b2fa3becd71c9bb141690130708079407e52a6e169cdacf42a467e09e0bda5` | 1 | 25,619 | 0 | 3 | House legacy PTR contains no recognized transaction rows |
| `9115809` | `0955f6961d01717aefa76635a84579d868b66d73cddbefdb9ec87f59bcc3fbbf` | 2 | 33,021 | 0 | 3 | House legacy PTR contains no recognized transaction rows |
| `9115811` | `2f4b2b6e98e044e6368a072275804bc61dda52f6f1e15c09ddb9074ea1b8952c` | 1 | 24,960 | 0 | 3 | House legacy PTR contains no recognized transaction rows |
| `9115814` | `fa5caf02b1b6d2b3238b28277a32fbfb0cdf1f452ee1e5246bf2a9719bdf04c6` | 2 | 50,158 | 0 | 3 | House legacy PTR contains no recognized transaction rows |
| `9115816` | `7b63c7eed7a3568ac352bba8e968a330768ddbe7590a632924fca11db18f9cc0` | 1 | 26,772 | 0 | 3 | House legacy PTR contains no recognized transaction rows |
| `9115901` | `4733b9c84441cefbcfdf929c349dfbb7010546c81d916c622c775610a8c48ccd` | 2 | 35,859 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116197` | `e89ec2f7ff2bd362f273002d70184a21a7da1da382b41e756fed163377802a71` | 2 | 55,695 | 0 | 4 | House PTR header does not match its archived identity |
| `9116212` | `758f38089886db42330f33a9fc8ed8b9960b0edf0c28247fa34afc1a2694a660` | 1 | 23,558 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116217` | `9a68e774d4a7948d523a8fecf4ce835fe0fa9dee34089a84b4234306beca3408` | 1 | 24,856 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116249` | `6578dc8f56ca3b087743fa89e5cf82140de1444a7f09f9d3709b5cd63fdb366e` | 1 | 25,594 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116256` | `20a57322d6853c46b523003c13d6c795d145eb0bca5796c5110c7b847d2a9edd` | 2 | 34,601 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116257` | `974a902f4dd6fe7eaeba9968d8fd2b70ba0ca8e522116fe5c0bcb1e617b736f5` | 1 | 19,583 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116260` | `40abd1de969ff344fab5f3c8950a17ebb8ab5df993ded910f00e48d0d47e385d` | 2 | 42,162 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116292` | `7bc49307a68e1dcfc8676fb885118da00f7fcf5b51a297eae214a65e564f209d` | 2 | 40,130 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116308` | `58443ef6f4d8ffb63524a94d2ed65b186f8eb7d38e5651947ea932751c8c0452` | 2 | 46,690 | 0 | 4 | House legacy PTR contains no recognized transaction rows |
| `9116326` | `be114957d03486636e639fe9d6c124c71ba8634e2f38f5dfb672eee92502ad2d` | 2 | 44,064 | 0 | 5 | House legacy PTR contains no recognized transaction rows |
| `9116331` | `58cefce89a3fe84a5d4893337e79b4bc9add769d02fac1869403456afd5a4343` | 2 | 33,662 | 0 | 7 | House legacy PTR contains no recognized transaction rows |

## 解析与处置结论

- 这是 House 纸面勾选框 PTR 扫描件，不是带 PDF 文本层的电子表格。旧错误只能证明此前解析器没有产出或识别身份失败，不能证明报告没有交易。
- 本工作环境没有可用的 Tesseract 可执行文件。为了不猜行数、日期、交易方向或金额，每份文件当前逐行状态都明确为 `not_observed_ocr_unavailable`，文件状态保持 `quarantined_unreplayed`。这 20 份的每一份都在清单里；没有默认为零行或静默排除。
- 由于没有 OCR 的逐行结果，本次不能提供真实固定 PDF 上的新旧行级差异账，也不能声称 20 份旧失败已恢复。后续固定样本重放必须补齐逐行 `parsed / quarantined / excluded` 处置、证据页/区域和行守恒，无法解释的行继续隔离。
- 当前前端 HTML 从标准 `transactions` 集合展示交易并据此提供人物、证券和日期窗口；这些报告若尚未解析，会造成潜在的交易覆盖缺口。HTML 样本内的记录不能推导这 20 份 PDF 的实际交易数，也不能由这次静态盘点计算具体少了多少行。

## 本轮代码改动

仅增加旧式扫描 PTR 的日期 OCR 字符归一化：对完整日期 token 处理常见的 `O/0`、`I/1` 混淆，识别点号/连字符分隔及紧凑的 6/8 位日期。日期仍需完整匹配且可被日历解析。解析器版本提升为 `house-legacy-checkbox-2026-09-date-normalization-v1`，使语义变化可辨识。

原有自动资格门保持不变：OCR 行置信度低于 85、缺少/无效通知日期、日期顺序异常、修订关系不闭合等记录仍会隔离。没有降低勾选识别阈值，也没有放宽字段完整度。当前日期变化仅由合成词级 fixture 覆盖，尚未在 20 份原件上证明能修复失败。

## 验收缺口

当前独立交付为固定样本清单与局部 parser 单元测试。执行真实原件 OCR 并形成 20 份逐行处置账，需要可用且固定版本的 Tesseract/渲染环境；此环境缺少该引擎，所以 D 尚未达到对原件逐行守恒和新旧差异逐项解释的验收条件。本报告将此明确列为未完成，而不是以测试合成样本代替真实样本结果。
