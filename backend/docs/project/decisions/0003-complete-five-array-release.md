# 0003：首发完整五数组与行情独立分支

日期：2026-09-21

## 背景

运营方确认 OGE Form 201 请求型报告不属于首发完整范围，并确认首发行情再分发授权问题已经解决。冻结消费契约仍要求 `people`、`transactions`、`reported_holdings`、`security_market_data` 与 `source_health` 五组规范数组。

完整行情约两年日线，不能安全塞入单个 8 MiB 看板分片。行情还具有独立更新频率和许可边界。

## 决定

- OGE 首发范围仅包含官方目录可以直接取得并完成自动资格判定的 278-T；Form 201 请求型报告保留覆盖计数，不阻塞首发。
- Alpaca SIP、`1Day`、`split` 日线写入独立受保护的 `market` 分支，每个 ticker 使用内容寻址分片。
- `main` manifest 固定记录已经发布并由 `published-market/<commit>` 保留的 `market_commit`；先发布行情提交，再发布引用它的披露快照。
- Dashboard 主分片只携带 90 日窗口需要的行情；人物与 ticker 窄分片声明市场依赖，读取器从同一固定 `market_commit` 组装完整五数组。
- 生产工作流必须同时满足显式授权变量和 GitHub production environment 中的 Alpaca secrets。密钥不进入日志、HTML或任何 Git ref。

## 后果

- 披露更新不再重复写入所有历史行情。
- 市场分支先成功、主分支后失败时，只留下未被 `main` 引用但仍可审计的已发布市场提交；旧 `main` 保持可读。
- 供应商无 bars 的 ticker 保持显式缺失并进入覆盖审计，不生成虚构价格。
- 发布前仍需用完整候选执行冻结处理器、渲染器和真实浏览器验收。
