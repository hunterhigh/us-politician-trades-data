# 初始架构

- 产品类型: `data`
- 运行方式: `scheduled`
- 技术适配器: `python`
- 部署画像: `github-actions`
- 风险等级: `critical`

## 选择理由

Python批处理，GitHub Actions 调度；公开GitHub固定提交读取。`main` 保存规范快照，`state` 保存检查点，`evidence` 保存官方原件，`review` 保存未复核机器产物；可选Worker留给以后私有分发；无在线数据库。

## 边界

本文件记录已批准的项目起点。具体产品架构应在第一阶段设计获批后补充。
