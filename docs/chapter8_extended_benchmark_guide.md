# 第8章扩展实验接入步骤

1. 先保留原始8个案例、67个日志的实验表和真实结果。它们仍承担无偏案例分析作用。
2. 在“实验设计与复现设置”之后插入 `chapter8_extended_benchmark_addition.tex`。
3. 不要把原表的8个工业案例与6个合成模型混成“14个工业场景”；正确说法是“8类原始案例、6个规模化合成模型，共14个不同BPMN”。
4. 正文应分别报告三组数据：原始人工实验67个、既有模型变异64个、合成规模实验90个。
5. 新增154个输入是按支持规则设计且经过严格筛选的受控基准。其154/154结果用于规则覆盖、回归和性能实验，不能称为未知样本准确率。
6. 有效性主结论继续使用原始案例的65/67可观测检出和44/67严格修复；规模结论使用90个合成日志的性能数据。
7. 在附录或补充材料中提供以下文件：
   - `experiments/extended_benchmark_manifest.json`
   - `experiments/extended_benchmark_manifest.csv`
   - `experiments/extended_benchmark_validation.json`
   - `experiments/extended_scale_benchmark.json`
   - `experiments/extended_scale_benchmark.csv`
8. 论文复现实验命令：

```powershell
.\.venv\Scripts\python.exe -B scripts\generate_extended_benchmark.py

.\.venv\Scripts\python.exe -B scripts\validate_extended_benchmark.py `
  --pipeline-smoke

.\.venv\Scripts\python.exe -B scripts\benchmark_extended_scale.py
```

9. 建议绘制一张横轴为消息流数量、纵轴为平均耗时的折线图，并增加P95折线。图中必须注明这是PM4Py预热后的分析流水线计时。
10. 最终编译时检查新增标签 `tab:扩展实验数据集构成`、`tab:规模递增合成BPMN性能` 是否重复。

