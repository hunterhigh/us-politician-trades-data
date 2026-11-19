# 数据访问与已弃用的公开客户端

仓库由所有者有意设为私有，必须保持私有。正式数据只通过已有带 token 的 skill 访问；该通道已能供前端使用，不重写 skill，也不新增 API、代理、CDN 或其他消费通道。字段需求见 [数据与前端契约](../docs/data-contract.md) 及固定的 `frontend-contract-v2/scripts/process_snapshot.py`。

`client/fetch_snapshot.py` 已弃用，执行时明确提示“公开读取已停止，请使用现有 token skill”，返回退出码 2；若传入 `--status-output` 则记录 `deprecated`。它不再发起网络请求。旧版只在解析 commit 时携带 token，下载数据文件不携带认证；给旧客户端设置 `GITHUB_TOKEN` 不能使它支持私有仓库。匿名 raw URL 返回 404 是私有化的预期结果，不是产品故障。

`client/snapshot_repo.py` 保留兼容导出。实际调用清单：`publication_smoke.py` 已停止执行旧 CLI，改以认证 ContentsTransport 复用 backend repository 类的契约校验；`frontend-e2e/build_fixture.py`、`known-gaps.e2e.mjs`、`source-freshness.e2e.mjs`、`ticker-matching.e2e.mjs` 仍以本地或 FakeTransport 使用共享类，不做匿名生产读取，不删除这些调用。旧 HTTPTransport 不用于生产监控。

## 凭据

消费 skill 的 fine-grained token 只需选择 `hunterhigh/us-politician-trades-data` 一个仓库，并授予 **Contents: Read**（Metadata: Read 由 GitHub 自动附带）；不需要 Contents 写入、Issues、Actions 或组织管理权限。凭据留在现有 skill 的 secret/环境变量配置中；本仓库不管理或更改该外部配置，也不把未知的变量名写成已确认事实。

仓库内健康监控由 `.github/workflows/health.yml` 将 GitHub 自动凭据 `${{ github.token }}` 注入 **`GITHUB_TOKEN`**，数据读取使用这个环境变量；同一 workflow 的 **`GH_TOKEN`** 用于既有状态/Issue/回退管理，不是给消费 skill 扩权。无需新增长期 PAT 或 secret。维护者本地验证时，仅在进程环境中提供 `GITHUB_TOKEN`，不把值作为命令参数或写入文件。

## 监控

监控通过已有认证 GitHub API 将 `main` 固定为 commit，使用 Contents API 的 raw media 响应读取同一提交的 manifest、索引和分片（包含引用的 market 提交），再校验现有哈希/字节数与前端投影、渲染契约。监控内部适配不对外提供新的数据入口，不改下游 skill。

匿名请求不参与产品健康判断。认证缺失、失效或认证读取 404 仍是实际访问问题，不能一概忽略 404。监控回执只记录状态、commit 和 snapshot 标识；任何 token 都不得写入代码、日志、workflow 输出或 Issue。
