# 只读快照代理：本地原型

此目录实现已发布快照的可选只读分发，不包含抓取、数据库、生产仓库或公网部署。交接包与交接说明是唯一消费基线；本目录测试只验证该可选代理实现，不引入或等待另一套前端。

## 本地验证

需要 Node.js 24、npm。依赖版本由 `package-lock.json` 固定。

```text
npm ci
npm run types
npm run typecheck
npm test
npm run types:check
npm run build:check
```

`build:check` 只做 Wrangler `deploy --dry-run`，不会部署。Wrangler 配置无路由、禁用 workers.dev 和 preview URLs；owner/repo 为占位值，密钥从未配置。`.dev.vars.example` 仅有无效本地占位字符串，不是可部署密钥。`secrets.required` 驱动生成的 Env 类型，不手写绑定接口。

## 读取协议及待对齐扩展

1. 消费者带 `Authorization: Bearer <proxy key>` 读取 `/v1/main/manifest.json`。
2. 代理读取固定配置仓库的 `refs/heads/main`，得到提交 SHA，并核验轻量标签 `refs/tags/published/<SHA>` 恰好指向该 SHA。
3. 代理从该 SHA 返回 manifest；响应头 `X-Snapshot-Commit` 返回固定 SHA。若 main 尚无发布标签则返回 503；生产者应原子推送 main 与标签。
4. 后续只能读取 `/v1/<40位小写SHA>/<允许路径>`，不能按 main 读取索引或分片。每次重新验证该 SHA 的发布标签，并在任何主分支分片读取前重新读取该固定SHA的manifest、拒绝demo或非法必填时间字段；标签删除会关闭后续访问。
5. 行情默认关闭。启用后，`market/` 路径必须由独立轻量标签 `published-market/<SHA>` 授权。标签在约定保留期内不得删除或移动。

当前严格把仓库main当数据发布入口。若同一main也直接合并普通代码PR，无发布标签的新HEAD会暂时503。上线前须保证每次main移动与完整发布标签原子更新，或协商将接口中的main映射到独立数据发布分支；不可悄悄把未发布的HEAD当作已发布快照。

允许路径只有 `manifest.json`、`people/<h2>/{index,<64位摘要>}.json`、`tickers/...`、`board/<64位摘要>.json` 和可选 `market/...`。不提供 demo、原件、审计、status、工作流或任意仓库/URL 代理。

标签机制是 **部署/版本访问权限**，不表示法律许可。发布者须在获准数据、真实数据和完整引用门禁通过后才创建 `published/` 标签，绝不能给 demo 或未验收提交加该标签。主入口与直接SHA分片请求均校验固定提交的manifest并拒绝 `is_demo` 非 false。代理不重跑处理器或逐文件审计；消费者仍必须校验分片内容哈希。独立market分支没有主manifest，因此市场读取仍依赖可信 `published-market/` 标签，默认关闭，生产者须独立保证非demo及有许可。行情许可及无行情内容门禁由生产端独立执行，`ALLOW_MARKET=false` 不是大文件内容扫描器，不能自动识别被错误塞进 board 的价格数据。

本轮生产器可能只允许 demo 本地构建，本代理面向正式读取协议而拒绝 demo。两者尚未做真实私有仓库或对方新读取器联调。测试全部采用模拟 GitHub 返回值；测试中的 `is_demo:false` 只是验证拒绝/接受分支的合成输入，不发布到真实仓库。

## 安全与资源边界

- 每个请求先鉴权；对等长 SHA-256 令牌摘要做常量时间比较。生产代理令牌应使用高熵随机值。当前单仓库/单凭据部署，未实现每用户角色体系。
- GitHub 凭据与代理 key 分离。只使用内容只读范围的 GitHub 凭据；代理没有远端写操作。请求 URL、Authorization、上游响应和异常详情不写应用日志。
- 所有响应 `private, no-store`，CDN 不缓存；没有共享缓存。GitHub API 请求禁用缓存、手动处理且拒绝重定向，密钥不发给任意主机。
- metadata/manifest 最大 16 KiB；所有index最大8 KiB；board/person/ticker/market内容分片最大16 MiB。后两类流式转发、不整体读进内存，按实际字节计数，不能用缺失或伪造Content-Length绕过；超限立即中断流而非成功返回截断JSON。每个上游请求含读取 body 的 10 秒截止，超时可能中断已开始的流，客户端必须丢弃不完整临时文件。
- manifest的 `generated_at` 和 `data_cutoff_at` 必填且须为有效带时区ISO日期时间；拒绝不存在的日期、无时区日期时间、日期字符串或任意文本。
- 不对分片执行服务端 SHA 验证，以保留流式行为；验证职责在实际消费者。代理只校验有限 manifest 字段，完整 schema/业务验证仍由发布者与消费者执行。
- main入口正常使用3次GitHub API请求（解析main、验证tag、读manifest）；固定SHA主分支索引/分片3次（验证tag、校验manifest、读文件）；固定SHA的manifest本身2次；可选市场文件2次（独立tag和文件）。这不等同于HTML原先只计客户端请求的成本，需纳入API额度测量。尚未缓存，不能承诺高并发能力或零成本。
- 固定SHA不保证永远可读：发布标签和远端对象仍需遵守保留/撤销政策。当前拒绝annotated tag，生产者必须创建lightweight tag。

## 参考

实现前核对的官方文档（2026-09-18）：

- [Workers best practices](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/)
- [Wrangler commands / types](https://developers.cloudflare.com/workers/wrangler/commands/)
- [Web Crypto constant-time comparison](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/)
- 配置字段按本地固定 Wrangler 的 `config-schema.json` 校验，运行时类型由 `wrangler types --strict-vars false` 生成。
