# 0001: 采用v0.3定时快照路线

## Context

用户要求简化并授权开始本地实现

## Decision

Python生产分片、Git原子发布；首发由公开GitHub固定提交直读，Worker保留为私有阶段可选项

## Rationale

减少常驻服务与数据库费用，保留证据和版本边界

## Alternatives

- 常驻API与PostgreSQL

## Consequences

- 用户查询不触发采集
- 新契约修订与许可仍需对齐

## Review when

需要请求时采集或快照超出容量预算
