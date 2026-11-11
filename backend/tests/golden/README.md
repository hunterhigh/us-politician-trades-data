# 黄金样本回归

`cases.json` 固定选样时的 evidence、review 提交、十类原件格式和报告编号。
PTR 按合格交易数、年报按合格持仓数选第一份；第二份选有隔离行且申报日期最近的另一份。
合格行数相同时按报告编号排序。缺少隔离报告时选日期最近的另一份；只有一份时收录一份。
当前共 19 份主样本；OGE 278-T 扫描格式只有一份。OGE 年报两份均未获生产资格。

`objects/` 保存从 evidence 复制的原件、获取元数据，以及现有的身份、OCR 和修订前件上下文。
Senate 年报修订样本同时带入其原始年报作为资格判定上下文。
重放使用现有解析与资格入口；扫描件沿用生产入口支持的、同一原件的已保存 OCR 输入。
每份 `expected.json` 保存解析得到的事实行、日期、处置和资格结果，测试按 UTF-8 字节对应的文本逐字比较。

运行黄金样本测试：

```sh
python -m unittest discover -s backend/tests/golden -p test_golden.py
```

有意修改解析或发布行为后，重新生成全部标准答案，并随 PR 提交：

```sh
python backend/tests/golden/run.py --regenerate
```
