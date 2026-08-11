# PAIS 跨组织协作异常检测与修复（论文第 7 章）

本仓库实现论文第 7 章的完整可解释流程：

1. 从 BPMN collaboration 中拆出每个 participant 的子流程，并根据表 7-3 计算消息模式；
2. 将全局日志按发送端/接收端投影，锁定潜在异常子流程；
3. 按表 7-4 和 Algorithm 6 展示每一步预分割，求解最小异常结构（MinAS）；
4. 按 Definition 7.16 合并相关 MinAS；
5. 按表 7-5 生成潜在修复方案，并用 Definition 7.17、正常日志和异常日志复验效果。

检测使用精确的消息模式成员判定。`--pattern-limit` 只限制展示用枚举，不会因并行交错过多而静默产生误报。

## 环境

推荐 Python 3.11 或 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 选择输入并运行

不带参数时，程序会依次让用户选择 `experiments` 下的案例、BPMN 和日志：

```powershell
.\.venv\Scripts\python.exe main.py
```

先查看所有输入：

```powershell
.\.venv\Scripts\python.exe main.py --list
```

非交互运行（适合复现实验）：

```powershell
.\.venv\Scripts\python.exe main.py `
  --case qingdao_port_simple `
  --log-file global_log_1.txt
```

也可以直接指定文件：

```powershell
.\.venv\Scripts\python.exe main.py `
  --bpmn experiments\quote_order\collaboration.bpmn `
  --log experiments\quote_order\global_log_2.txt
```

程序开头会明确打印实际使用的绝对路径、文件大小和 SHA-256。完整中间结果同时写入
`output/<case>/<log>/analysis-report.json`，包括每个日志投影、closed/open 状态、预分割路径、MinAS、修复候选和复验统计。

常用选项：

- `--summary`：隐藏逐节点预分割过程，但保留最终 MinAS 与修复结果；
- `--output-dir PATH`：指定运行时文件和 JSON 报告目录；
- `--no-json`：只打印终端报告；
- `--pattern-limit N`：限制展示消息模式数量，不影响检测；
- `--behavior-limit N`：限制 Definition 7.17 的子树全集枚举，超过时明确报告“未知”。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试包含论文第 7 章港口子流程的消息模式、缺失并行分支 MinAS、顺序逆序 MinAS、表 7-5 修复规则，以及仓库 BPMN/日志的端到端运行。

## 扩展实验基准

为扩大第 8 章实验规模，仓库提供可复现的扩展基准生成器。默认固定随机种子
`20260809`，生成 6 个由 5--20 个参与方、12--96 条消息流组成的结构化合成
BPMN，每个模型配套 15 个日志；同时为 8 个既有 BPMN 各追加 8 个
`global_log_100.txt` 起的日志。生成器不会覆盖原有低编号日志。

```powershell
.\.venv\Scripts\python.exe -B scripts\generate_extended_benchmark.py
```

合成案例均衡包含消息缺失、顺序逆转和互斥分支同时执行三类异常。既有案例通过
对原始正常迹进行删除、相邻换序或发送/接收可见性变异得到新增异常。每个文件都
记录随机种子和注入操作；`experiments/extended_benchmark_manifest.json` 与 CSV
清单保存文件 SHA-256、注入真值、检出的参与方、MinAS 和严格有效规则。

独立复核全部新增文件，并按异常类型对每个合成案例运行完整流水线冒烟测试：

```powershell
.\.venv\Scripts\python.exe -B scripts\validate_extended_benchmark.py `
  --pipeline-smoke
```

验证结果写入 `experiments/extended_benchmark_validation.json`，完整流水线报告写入
`output/extended_benchmark_smoke/`。论文中应把合成样本、既有模型变异样本和原始
人工案例分组报告，不能把由同一生成器产生的日志当作互相独立的工业案例。

对 6 个合成规模的全部 90 个日志执行一次PM4Py预热后的统一性能基准：

```powershell
.\.venv\Scripts\python.exe -B scripts\benchmark_extended_scale.py
```

逐次结果和按规模汇总分别写入 `experiments/extended_scale_benchmark.csv` 与 JSON。
这里采用报告内部的分析流水线计时，不包含Python/PM4Py导入、JSON写入和终端打印；
该口径应与每次新进程冷启动的命令行总耗时分开报告。

## 模型与日志约束

- BPMN 必须是结构化、无循环的 collaboration；每个 participant 必须引用一个 process。
- 与论文一致，每个消息活动在流程树中最多出现一次，messageFlow 名称必须唯一。
- 日志每行一条消息迹，逗号分隔；空行和 `#` 注释会被忽略。
- `M1_s` 只对发送方可见，`M1_r` 只对接收方可见；无后缀 `M1` 按兼容模式投影给两端。
- 程序只输出“潜在修复方案”和验证证据，不会自动覆盖原 BPMN。
