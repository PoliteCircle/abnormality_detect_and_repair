from __future__ import annotations

from .model import node_to_expression, path_to_string, tree_lines
from .pipeline import AnalysisReport


def _fmt_trace(trace: tuple[str, ...]) -> str:
    return "<epsilon>" if not trace else "<" + ", ".join(trace) + ">"


def _sample(traces, limit: int = 20) -> str:
    ordered = sorted(traces, key=lambda trace: (len(trace), trace))
    shown = ", ".join(_fmt_trace(trace) for trace in ordered[:limit])
    if len(ordered) > limit:
        shown += f", ...（另有 {len(ordered) - limit} 条）"
    return shown or "（空集）"


def _location(path, child_slice=None) -> str:
    base = path_to_string(path)
    if child_slice is None:
        return base
    start, end = child_slice
    return f"{base}/children[{start + 1}:{end + 1}]"


def print_report(report: AnalysisReport, *, detailed: bool = True) -> None:
    print("\n" + "=" * 96)
    print("第7章：外部协作异常相容性的检测及修复")
    print("=" * 96)
    print("[输入确认]")
    print(f"  BPMN : {report.inputs.bpmn_path}")
    print(f"         size={report.inputs.bpmn_size} bytes, sha256={report.inputs.bpmn_sha256}")
    print(f"  LOG  : {report.inputs.log_path}")
    print(
        f"         size={report.inputs.log_size} bytes, traces={report.inputs.log_trace_count}, "
        f"sha256={report.inputs.log_sha256}"
    )
    print(f"  运行时规范化 BPMN: {report.inputs.normalised_bpmn_path}")
    print("  说明: 检测使用精确成员判定；pattern_limit 只限制展示枚举，不影响正确性。")

    for process_index, analysis in enumerate(report.processes, start=1):
        model = analysis.model
        print("\n" + "#" * 96)
        print(
            f"[子流程 {process_index}/{len(report.processes)}] "
            f"participant={model.participant_name!r}, process_id={model.process_id!r}"
        )
        print(f"  发送消息 MEo = {sorted(model.sends)}")
        print(f"  接收消息 MEi = {sorted(model.receives)}")
        print(f"  消息流程树    = {node_to_expression(model.tree)}")
        if detailed:
            print("  节点及 NM 集合:")
            for line in tree_lines(model.tree):
                print("    " + line)

        marker = "（达到展示上限，已截断）" if analysis.patterns.truncated else "（完整）"
        print("\n  [7.1.1 消息模式]")
        print(
            f"    closed={len(analysis.patterns.closed)}, open={len(analysis.patterns.open)} {marker}"
        )
        print(f"    closed sample: {_sample(analysis.patterns.closed)}")
        print(f"    open sample  : {_sample(analysis.patterns.open)}")

        print("\n  [7.1.2 锁定潜在异常子流程]")
        for check in analysis.checks:
            status = (
                "合法（" + "/".join(check.accepted_modes) + "）"
                if check.accepted
                else "异常：投影不属于 MP"
            )
            print(
                f"    trace#{check.source_index} (log line {check.line_number}): "
                f"global={_fmt_trace(check.global_trace)}"
            )
            print(f"      projection={_fmt_trace(check.projected_trace)} -> {status}")

        if not analysis.violating:
            print("    结论：该子流程不是潜在异常子流程。")
            continue

        print("    结论：该子流程被锁定；下面仅对异常投影执行 Algorithm 6。")
        print("\n  [7.2 求解最小异常结构]")
        for diagnosis in analysis.diagnoses:
            check = diagnosis.check
            print(
                f"    异常 trace#{check.source_index}: projected={_fmt_trace(check.projected_trace)}"
            )
            if detailed:
                for step_index, step in enumerate(diagnosis.result.steps, start=1):
                    verdict = "成功" if step.success else "失败 -> 当前节点为 MinAS"
                    print(
                        f"      step {step_index}: {path_to_string(step.path)} "
                        f"{step.expression}, tr={_fmt_trace(step.trace)}"
                    )
                    print(f"        预分割{verdict}: {step.reason}")
                    for assignment in step.assignments:
                        print(
                            f"        -> {path_to_string(assignment.child_path)} "
                            f"{assignment.child_expression}: {_fmt_trace(assignment.trace)}"
                        )
            for item in diagnosis.result.minimal_structures:
                print(
                    f"      MinAS: {_location(item.path, item.child_slice)} "
                    f"{node_to_expression(item.node)}, assigned={_fmt_trace(item.assigned_trace)}"
                )
                print(f"        原因: {item.reason}")

        print("\n  [7.3 合并异常结构并求解潜在修复方案]")
        for scope_analysis in analysis.scopes:
            scope = scope_analysis.scope
            label = "Definition 7.16 合并结构" if scope.kind == "merged" else "单个/同路径 MinAS"
            print(
                f"    scope={_location(scope.path, scope.child_slice)} ({label}), "
                f"node={node_to_expression(scope.node)}"
            )
            print(f"      异常片段={[_fmt_trace(trace) for trace in scope.observations]}")
            if not scope_analysis.candidates:
                print("      表7-5没有与该结构形态完全对应的简洁候选；保留为人工分析项。")
                continue
            for rank, candidate in enumerate(scope_analysis.candidates, start=1):
                proof = (
                    "满足"
                    if candidate.definition_717_satisfied is True
                    else "不满足"
                    if candidate.definition_717_satisfied is False
                    else "未知（枚举达到上限）"
                )
                print(
                    f"      candidate#{rank} / 表7-5方案{candidate.rule_id}: {candidate.title}"
                )
                print(f"        替换: {candidate.before_expression} -> {candidate.after_expression}")
                print(f"        理由: {candidate.rationale}")
                print(
                    f"        异常片段覆盖: {candidate.scope_observations_covered}/"
                    f"{candidate.scope_observations_total}; Definition 7.17: {proof}"
                )
                print(
                    f"        完整流程复验: normal {candidate.normal_after_pass}/{candidate.normal_total}, "
                    f"abnormal {candidate.abnormal_after_pass}/{candidate.abnormal_total}"
                )
                print(f"        编辑代价={candidate.edit_cost}")
                for warning in candidate.warnings:
                    print(f"        警告: {warning}")

    print("\n" + "=" * 96)
    print("[执行汇总]")
    print(f"  子流程总数: {len(report.processes)}")
    print(f"  潜在异常子流程: {report.violating_process_count}")
    print(f"  总耗时: {report.elapsed_seconds:.3f} 秒")
    if report.json_path:
        print(f"  机器可读详细报告: {report.json_path}")
    print("=" * 96)
