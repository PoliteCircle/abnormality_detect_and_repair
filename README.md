# 跨组织协作异常检测与修复实验

本仓库提供一套独立的可复现实验流程：从 BPMN collaboration 模型和全局交互日志中检测潜在异常子流程，定位最小异常结构（MinAS），生成候选修复方案，并复验修复效果。

## 实验流程

程序依次完成 BPMN 规范化、participant 子流程拆分、消息流程树构建、closed/open 消息模式计算、全局日志投影、异常子流程锁定、MinAS 求解、异常结构合并、修复生成和严格行为复验。

原始 BPMN 和日志不会被修改。规范化 BPMN、拆分 process、运行时文件和 JSON 报告写入 `output/` 或命令指定的输出目录。

## 目录结构

```text
main.py                         命令行入口
analysis/                       核心分析模块
experiments/                    BPMN、全局日志和扩展基准清单
generated_bpmn/                 规范化 BPMN 示例
scripts/                        基准生成、验证和性能测试脚本
tests/                          单元测试与集成测试
legacy/                         早期原型脚本，仅供历史对照
output/                         运行时文件和分析报告
```

主线实验代码统一位于 `analysis/`。`legacy/` 中的脚本保留历史实现，不参与主线命令、扩展基准和测试。

## 环境配置

推荐 Python 3.11 或 Python 3.12。BPMN 读取和流程树转换需要 PM4Py。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install "pm4py>=2.7"
```

环境检查：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import pm4py; print(pm4py.__version__)"
```

## 输入数据

每个案例位于 `experiments/<case>/`，通常包含一个 `collaboration.bpmn` 和若干 `global_log_*.txt`。BPMN 应包含 collaboration、participant、process、任务节点和 messageFlow。

日志每行表示一条全局消息轨迹，消息名以逗号分隔；空行会被忽略：

```text
M1_s,M1_r,M2_s,M2_r
M1,M2,M3
```

`_s` 表示发送端观测，`_r` 表示接收端观测。未带后缀的消息名根据 participant 的发送集合和接收集合进行投影。以 `#` 开头的日志行会被跳过。

查看可用输入：

```powershell
.\.venv\Scripts\python.exe main.py --list
```

现有案例包括 8 个既有案例和 6 个规模递增的合成案例。`easy_example` 只有 BPMN 文件，没有日志，不能执行完整分析。

## 单案例运行

交互式运行：

```powershell
.\.venv\Scripts\python.exe main.py
```

指定案例和日志：

```powershell
.\.venv\Scripts\python.exe main.py `
  --case qingdao_port_simple `
  --log-file global_log_1.txt
```

直接指定文件：

```powershell
.\.venv\Scripts\python.exe main.py `
  --bpmn experiments\quote_order\collaboration.bpmn `
  --log experiments\quote_order\global_log_2.txt
```

主要选项：

```text
--list                         列出可用输入
--case NAME                    指定案例目录
--bpmn-name NAME               指定案例中的 BPMN 文件
--log-file NAME                指定案例中的日志文件
--bpmn PATH --log PATH         直接指定 BPMN 和日志，必须同时出现
--experiments-dir PATH         指定实验根目录
--output-dir PATH              指定输出目录
--report-name NAME             指定 JSON 报告文件名
--pattern-limit N              消息模式展示上限，默认 10000
--behavior-limit N             行为验证的枚举上限，默认 20000
--summary                      隐藏逐节点预分割细节
--no-json                      不写 JSON 报告
```

`pattern-limit` 只限制模式展示数量，不改变精确检测结果。`behavior-limit` 达到上限时，行为验证结果可能为“未知”，应提高上限后重新运行。默认报告路径为 `output/<case>/<log-file-without-extension>/analysis-report.json`。

## 逐案例实验

以下 PowerShell 脚本为每个案例的每个日志创建独立输出目录：

```powershell
$python = ".\.venv\Scripts\python.exe"
$root = (Get-Location).Path
foreach ($case in Get-ChildItem experiments -Directory) {
  $bpmn = Get-ChildItem $case.FullName -Filter "*.bpmn" -File | Select-Object -First 1
  $logs = Get-ChildItem $case.FullName -Filter "global_log_*.txt" -File
  if ($null -eq $bpmn -or $logs.Count -eq 0) { continue }
  foreach ($log in $logs) {
    $out = Join-Path $root ("output\all-cases\" + $case.Name + "\" + $log.BaseName)
    & $python main.py --bpmn $bpmn.FullName --log $log.FullName --output-dir $out --summary
    if ($LASTEXITCODE -ne 0) { throw "analysis failed: $($log.FullName)" }
  }
}
```

## 扩展基准实验

默认基准使用随机种子 `20260809`，为 6 个合成案例各生成 15 条日志，并为 8 个既有案例各追加 8 条日志：

```powershell
.\.venv\Scripts\python.exe scripts\generate_extended_benchmark.py
```

生成文件为 `experiments/extended_benchmark_manifest.json` 和 `experiments/extended_benchmark_manifest.csv`。自定义参数：

```powershell
.\.venv\Scripts\python.exe scripts\generate_extended_benchmark.py `
  --seed 20260809 `
  --synthetic-logs-per-case 15 `
  --existing-logs-per-case 8
```

验证文件哈希、轨迹数量、异常类型、participant、MinAS、修复规则和严格修复结果：

```powershell
.\.venv\Scripts\python.exe scripts\validate_extended_benchmark.py
.\.venv\Scripts\python.exe scripts\validate_extended_benchmark.py --pipeline-smoke
```

验证报告默认写入 `experiments/extended_benchmark_validation.json`，无错误时应显示 `errors: 0`。

执行规模性能实验：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_extended_scale.py
```

性能结果写入 `experiments/extended_scale_benchmark.json` 和 `experiments/extended_scale_benchmark.csv`。计时字段为 `AnalysisReport.elapsed_seconds`，不包含 Python/PM4Py 导入、JSON 序列化和终端渲染。

## 报告字段

| 字段 | 含义 |
| --- | --- |
| `inputs` | 输入路径、大小、轨迹数和 SHA-256 |
| `tree_expression` | participant 消息流程树 |
| `patterns` | closed/open 消息模式 |
| `trace_checks` | 全局轨迹投影和接受结果 |
| `diagnoses` | 预分割步骤与 MinAS |
| `scopes` | 合并后的修复范围 |
| `candidates` | 候选修复及验证结果 |
| `behavior_satisfied` | 是否满足严格行为约束 |
| `normal_log` / `abnormal_log` | 修复后正常、异常日志通过数量 |
| `summary` | 子流程数、异常子流程数和耗时 |

严格修复的判据为 `behavior_satisfied == true`，且修复后正常日志全部通过、异常日志全部通过。结果为 `null` 或“未知”时，表示行为枚举达到上限，不能作为已证明的严格修复。

## 基线规模与验收标准

| 数据来源 | 案例数 | 每案例日志数 | 日志总数 |
| --- | ---: | ---: | ---: |
| 既有 BPMN 异常变异 | 8 | 8 | 64 |
| 规模递增合成 BPMN | 6 | 15 | 90 |
| 合计 | 14 | - | 154 |

合成案例分别包含 5、7、9、12、16、20 个 participant，以及 12、20、32、48、72、96 条 messageFlow。每个合成案例的异常类型应均衡分布，每种类型 5 条日志。

实验验收条件：

1. `validate_extended_benchmark.py` 返回码为 0，且 `errors: 0`。
2. manifest 中的 BPMN 和日志 SHA-256 与实际文件一致。
3. 154 条基准记录的 `detected` 和 `strict_repair_success` 均为真。
4. `--pipeline-smoke` 完成，抽样流水线均检测到异常并找到严格修复候选。
5. 全部单元测试和集成测试通过。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖 BPMN 解析回退、日志投影、消息模式、MinAS 诊断、修复候选、既有案例端到端流程和扩展基准清单。验证脚本与测试建议串行执行，避免同时写入 `output/` 或临时目录。

## 实验记录

正式实验记录应保存操作系统、处理器、Python 和 PM4Py 版本，执行命令、随机种子、枚举上限，两个基准 manifest、两个基准报告、代表性案例的 `analysis-report.json`、终端输出以及测试完整输出。
