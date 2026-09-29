# House PTR 离线影子账本（2026-09-29）

本入口读取三个固定提交：运行代码 `code_commit`、House 原件 `evidence_commit`、提取及资格结果 `review_commit`。`code_commit` 必须等于当前干净签出的 HEAD。输入按 `document_id` 选择 2026 年 House PTR；每份 PDF 字节重新计算 SHA-256，并与归档元数据、路径、提取件和资格件绑定。输出只写指定的全新本地目录，不访问生产写入口。

最新前端基准为用户给出的 `C:\Users\admin\Downloads\politician-disclosures (3).html`，SHA-256 `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4`。此账本的 `candidate_rows.json` 是来源观察，不是前端五数组数据或生产候选发布。

入口：`PYTHONPATH=backend/src python -m unison_snapshot.house_shadow --repo . --code-commit <HEAD的40位SHA> --evidence-commit 80f086267677b8fde34314f24024bdc505d97cb0 --review-commit 9749718e5ae837057db7c031d431714fc72988d1 --document-id 20019182 --run-id <唯一运行ID> --output-dir <新的本地目录>`。重复 `--document-id` 可形成有限批次。输出为 `manifest.json` 与 `candidate_rows.json`，前者以 `artifact_type=candidate_rows` 记录后者的 SHA-256，可直接作为现有 `pipeline_qa.bundle_cli` 的来源运行目录；现有目录拒绝覆盖。

`.github/workflows/house-shadow.yml` 提供独立的手动影子入口。它只给 `contents: read` 权限，按完整 SHA 读取且核对 `evidence`/`review` 分支祖先关系，最多选择 25 个 2026 House PTR 文档，重新验证产物哈希并上传 14 天 artifact。它不使用生产环境、不触发 House 采集工作流，也不修改任何 Git 分支。工作流的默认提交是上述固定样本，运行者可以改为其他仍能在对应分支历史中找到的完整提交。

对有提取件的报告，所有 `extraction_id` 必须恰好进入资格件的合格或隔离集合之一，且数量和资格摘要一致。合格行的必要字段还要与资格结果逐项一致。每一行保留来源 SHA、页面定位、原始/规范值和原提取证据；缺少页定位、重复 ID、来源不匹配或漏处置时整批失败关闭。当前统一账本只给提取结果的已观察行计数；解析器可能漏检的物理行仍需后续 OCR/版式审计，不能由行守恒证明完整覆盖。

对只有固定失败记录的扫描件，文档状态为 `failed`，原因保留旧失败文字，`row_count_status=unknown_parse_failed`。它不生成候选行，也不将报告判定为真正的零交易。只有提取件含官方明确零交易声明时才允许 `no_rows`。缺少提取件和失败记录、两者同时存在或缺失资格件均失败关闭。

固定离线样本使用 `evidence`=`80f086267677b8fde34314f24024bdc505d97cb0` 与 `review`=`9749718e5ae837057db7c031d431714fc72988d1`：`20019182` 的 1 份已解析报告产生 8 条合格候选行；[20 份扫描失败报告](house-ptr-failure-corpus-audit-2026-09-29.md)逐份核对 PDF 哈希后产生 20 个 `failed` 文档和 0 条已观察候选行，各文档行数仍未知。此样本并未恢复扫描件交易，也未覆盖 House 全量归档或链接生产 Actions。
