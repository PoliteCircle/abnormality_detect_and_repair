from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .bpmn import load_process_models
from .diagnosis import (
    DiagnosisResult,
    MinimalAbnormalStructure,
    RepairScope,
    SplitStep,
    build_repair_scopes,
    find_minimal_abnormal_structures,
)
from .logs import load_global_log, project_global_trace
from .model import ProcessModel, Trace, node_to_expression, path_to_string, tree_lines
from .patterns import PatternEnumeration, accepted_modes, enumerate_patterns
from .repairs import RepairCandidate, generate_repair_candidates


@dataclass(frozen=True, slots=True)
class InputSummary:
    bpmn_path: Path
    bpmn_size: int
    bpmn_sha256: str
    log_path: Path
    log_size: int
    log_sha256: str
    log_trace_count: int
    normalised_bpmn_path: Path


@dataclass(frozen=True, slots=True)
class TraceCheck:
    source_index: int
    line_number: int
    global_trace: Trace
    projected_trace: Trace
    accepted_modes: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return bool(self.accepted_modes)


@dataclass(frozen=True, slots=True)
class TraceDiagnosis:
    check: TraceCheck
    result: DiagnosisResult


@dataclass(frozen=True, slots=True)
class ScopeAnalysis:
    scope: RepairScope
    candidates: tuple[RepairCandidate, ...]


@dataclass(frozen=True, slots=True)
class ProcessAnalysis:
    model: ProcessModel
    patterns: PatternEnumeration
    checks: tuple[TraceCheck, ...]
    diagnoses: tuple[TraceDiagnosis, ...]
    scopes: tuple[ScopeAnalysis, ...]

    @property
    def violating(self) -> bool:
        return any(not check.accepted for check in self.checks)


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    inputs: InputSummary
    processes: tuple[ProcessAnalysis, ...]
    pattern_limit: int
    behavior_limit: int
    elapsed_seconds: float
    json_path: Path | None = None

    @property
    def violating_process_count(self) -> int:
        return sum(process.violating for process in self.processes)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_analysis(
    bpmn_path: Path,
    log_path: Path,
    output_dir: Path,
    *,
    pattern_limit: int = 10_000,
    behavior_limit: int = 20_000,
    report_name: str = "analysis-report.json",
    write_json: bool = True,
) -> AnalysisReport:


    started = time.perf_counter()
    bpmn_path = bpmn_path.resolve()
    log_path = log_path.resolve()
    output_dir = output_dir.resolve()
    runtime_dir = output_dir / "runtime"
    normalised, models = load_process_models(bpmn_path, runtime_dir)
    log = load_global_log(log_path)

    analyses: list[ProcessAnalysis] = []
    for model in models:
        patterns = enumerate_patterns(
            model.tree,
            model.sends,
            model.receives,
            limit=pattern_limit,
        )
        checks: list[TraceCheck] = []
        for source_index, log_trace in enumerate(log, start=1):
            projected = project_global_trace(log_trace.tokens, model.sends, model.receives)
            modes = tuple(sorted(accepted_modes(model.tree, projected, model.sends, model.receives)))
            checks.append(
                TraceCheck(
                    source_index,
                    log_trace.line_number,
                    log_trace.tokens,
                    projected,
                    modes,
                )
            )

        diagnoses: list[TraceDiagnosis] = []
        all_minimal: list[MinimalAbnormalStructure] = []
        for check in checks:
            if check.accepted:
                continue
            result = find_minimal_abnormal_structures(
                model.tree,
                check.projected_trace,
                source_index=check.source_index,
                line_number=check.line_number,
                global_trace=check.global_trace,
            )
            diagnoses.append(TraceDiagnosis(check, result))
            all_minimal.extend(result.minimal_structures)

        normal_traces = tuple(check.projected_trace for check in checks if check.accepted)
        abnormal_traces = tuple(check.projected_trace for check in checks if not check.accepted)
        scopes: list[ScopeAnalysis] = []
        for scope in build_repair_scopes(model.tree, all_minimal):
            candidates = generate_repair_candidates(
                model,
                scope,
                normal_traces=normal_traces,
                abnormal_traces=abnormal_traces,
                behavior_limit=behavior_limit,
            )
            scopes.append(ScopeAnalysis(scope, candidates))

        analyses.append(
            ProcessAnalysis(
                model,
                patterns,
                tuple(checks),
                tuple(diagnoses),
                tuple(scopes),
            )
        )

    inputs = InputSummary(
        bpmn_path=bpmn_path,
        bpmn_size=bpmn_path.stat().st_size,
        bpmn_sha256=_sha256(bpmn_path),
        log_path=log_path,
        log_size=log_path.stat().st_size,
        log_sha256=_sha256(log_path),
        log_trace_count=len(log),
        normalised_bpmn_path=normalised.resolve(),
    )
    json_path = output_dir / report_name if write_json else None
    report = AnalysisReport(
        inputs=inputs,
        processes=tuple(analyses),
        pattern_limit=pattern_limit,
        behavior_limit=behavior_limit,
        elapsed_seconds=time.perf_counter() - started,
        json_path=json_path,
    )
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w", encoding="utf-8") as stream:
            json.dump(report_to_dict(report), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return report


def _trace(trace: Trace) -> list[str]:
    return list(trace)


def _step_to_dict(step: SplitStep) -> dict[str, Any]:
    return {
        "path": path_to_string(step.path),
        "expression": step.expression,
        "trace": _trace(step.trace),
        "success": step.success,
        "reason": step.reason,
        "assignments": [
            {
                "path": path_to_string(item.child_path),
                "expression": item.child_expression,
                "trace": _trace(item.trace),
            }
            for item in step.assignments
        ],
    }


def _candidate_to_dict(candidate: RepairCandidate) -> dict[str, Any]:
    return {
        "repair_rule": candidate.rule_id,
        "title": candidate.title,
        "rationale": candidate.rationale,
        "scope_path": path_to_string(candidate.scope_path),
        "scope_child_slice": list(candidate.scope_child_slice) if candidate.scope_child_slice else None,
        "before_expression": candidate.before_expression,
        "after_expression": candidate.after_expression,
        "repaired_process_expression": candidate.repaired_process_expression,
        "edit_cost": candidate.edit_cost,
        "model_behavior_preserved": candidate.model_behavior_preserved,
        "scope_observations": {
            "covered": candidate.scope_observations_covered,
            "total": candidate.scope_observations_total,
        },
        "normal_log": {
            "after_pass": candidate.normal_after_pass,
            "total": candidate.normal_total,
        },
        "abnormal_log": {
            "after_pass": candidate.abnormal_after_pass,
            "total": candidate.abnormal_total,
        },
        "behavior_satisfied": candidate.behavior_satisfied,
        "warnings": list(candidate.warnings),
    }


def report_to_dict(report: AnalysisReport) -> dict[str, Any]:
    return {
        "inputs": {
            "bpmn": {
                "path": str(report.inputs.bpmn_path),
                "size": report.inputs.bpmn_size,
                "sha256": report.inputs.bpmn_sha256,
            },
            "log": {
                "path": str(report.inputs.log_path),
                "size": report.inputs.log_size,
                "sha256": report.inputs.log_sha256,
                "trace_count": report.inputs.log_trace_count,
            },
            "normalised_bpmn": str(report.inputs.normalised_bpmn_path),
        },
        "configuration": {
            "pattern_limit": report.pattern_limit,
            "behavior_limit": report.behavior_limit,
        },
        "summary": {
            "process_count": len(report.processes),
            "violating_process_count": report.violating_process_count,
            "elapsed_seconds": report.elapsed_seconds,
        },
        "processes": [
            {
                "participant": analysis.model.participant_name,
                "process_id": analysis.model.process_id,
                "sends": sorted(analysis.model.sends),
                "receives": sorted(analysis.model.receives),
                "tree_expression": node_to_expression(analysis.model.tree),
                "tree": tree_lines(analysis.model.tree),
                "patterns": {
                    "closed_count": len(analysis.patterns.closed),
                    "open_count": len(analysis.patterns.open),
                    "truncated": analysis.patterns.truncated,
                    "closed": [_trace(trace) for trace in sorted(analysis.patterns.closed)],
                    "open": [_trace(trace) for trace in sorted(analysis.patterns.open)],
                },
                "trace_checks": [
                    {
                        "source_index": check.source_index,
                        "line_number": check.line_number,
                        "global": _trace(check.global_trace),
                        "projected": _trace(check.projected_trace),
                        "accepted": check.accepted,
                        "accepted_modes": list(check.accepted_modes),
                    }
                    for check in analysis.checks
                ],
                "diagnoses": [
                    {
                        "source_index": diagnosis.check.source_index,
                        "steps": [_step_to_dict(step) for step in diagnosis.result.steps],
                        "minimal_abnormal_structures": [
                            {
                                "path": path_to_string(item.path),
                                "expression": node_to_expression(item.node),
                                "assigned_trace": _trace(item.assigned_trace),
                                "reason": item.reason,
                                "child_slice": list(item.child_slice) if item.child_slice else None,
                            }
                            for item in diagnosis.result.minimal_structures
                        ],
                    }
                    for diagnosis in analysis.diagnoses
                ],
                "repair_scopes": [
                    {
                        "path": path_to_string(scope.scope.path),
                        "child_slice": list(scope.scope.child_slice) if scope.scope.child_slice else None,
                        "kind": scope.scope.kind,
                        "expression": node_to_expression(scope.scope.node),
                        "observations": [_trace(trace) for trace in scope.scope.observations],
                        "source_paths": [path_to_string(path) for path in scope.scope.source_paths],
                        "source_indices": list(scope.scope.source_indices),
                        "candidates": [_candidate_to_dict(candidate) for candidate in scope.candidates],
                    }
                    for scope in analysis.scopes
                ],
            }
            for analysis in report.processes
        ],
        "json_path": str(report.json_path) if report.json_path else None,
    }
