from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from chapter7.bpmn import load_process_models
from chapter7.logs import load_global_log
from chapter7.pipeline import run_analysis
from scripts.generate_extended_benchmark import _evaluate_dataset


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_report_success(report) -> bool:
    scopes = [scope for process in report.processes for scope in process.scopes]
    if not scopes:
        return False
    for scope in scopes:
        if not any(
            candidate.definition_717_satisfied is True
            and candidate.normal_after_pass == candidate.normal_total
            and candidate.abnormal_after_pass == candidate.abnormal_total
            for candidate in scope.candidates
        ):
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立复核扩展BPMN/日志基准及其真值清单。")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "experiments" / "extended_benchmark_manifest.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY_ROOT / "experiments" / "extended_benchmark_validation.json",
    )
    parser.add_argument(
        "--pipeline-smoke",
        action="store_true",
        help="每个既有案例跑1份完整流水线，每个合成案例按异常类型各跑1份",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    by_case: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_case[entry["case"]].append(entry)

    errors: list[str] = []
    checked = 0
    smoke_checked = 0
    results_by_case: dict[str, dict] = {}
    anomaly_counts: Counter[str] = Counter()
    origin_counts: Counter[str] = Counter()

    with tempfile.TemporaryDirectory(prefix="pais-benchmark-validation-") as temp:
        runtime_root = Path(temp)
        for case_name in sorted(by_case):
            case_entries = by_case[case_name]
            bpmn_path = REPOSITORY_ROOT / case_entries[0]["bpmn_file"]
            if _sha256(bpmn_path) != case_entries[0]["bpmn_sha256"]:
                errors.append(f"{case_name}: BPMN SHA-256 mismatch")
                continue
            try:
                _, models = load_process_models(bpmn_path, runtime_root / case_name)
            except Exception as exc:  # validation must report the failing case
                errors.append(f"{case_name}: BPMN load failed: {exc}")
                continue

            case_detected = 0
            case_strict = 0
            for entry in case_entries:
                checked += 1
                anomaly_counts[entry["anomaly_type"]] += 1
                origin_counts[entry["model_origin"]] += 1
                log_path = REPOSITORY_ROOT / entry["log_file"]
                if _sha256(log_path) != entry["log_sha256"]:
                    errors.append(f"{entry['log_file']}: log SHA-256 mismatch")
                    continue
                traces = tuple(item.tokens for item in load_global_log(log_path))
                if len(traces) != entry["normal_trace_count"] + entry["abnormal_trace_count"]:
                    errors.append(f"{entry['log_file']}: trace count mismatch")
                    continue
                normals = traces[: entry["normal_trace_count"]]
                abnormal = traces[-1]
                try:
                    evaluation = _evaluate_dataset(models, normals, abnormal)
                except Exception as exc:
                    errors.append(f"{entry['log_file']}: evaluation failed: {exc}")
                    continue
                if evaluation.detected:
                    case_detected += 1
                if evaluation.strict_repair_success:
                    case_strict += 1
                if evaluation.detected != entry["detected"]:
                    errors.append(f"{entry['log_file']}: detected flag differs from manifest")
                if evaluation.strict_repair_success != entry["strict_repair_success"]:
                    errors.append(f"{entry['log_file']}: strict repair flag differs from manifest")
                if list(evaluation.affected_participants) != entry["detected_participants"]:
                    errors.append(f"{entry['log_file']}: detected participants differ from manifest")
                actual_minas = {
                    name: list(values)
                    for name, values in evaluation.minimal_abnormal_structures.items()
                }
                if actual_minas != entry["detected_minas"]:
                    errors.append(f"{entry['log_file']}: MinAS differs from manifest")
                actual_rules = {
                    name: list(values) for name, values in evaluation.strict_rule_ids.items()
                }
                if actual_rules != entry["strict_rule_ids"]:
                    errors.append(f"{entry['log_file']}: repair rules differ from manifest")

            results_by_case[case_name] = {
                "entry_count": len(case_entries),
                "participant_count": case_entries[0]["participant_count"],
                "message_flow_count": case_entries[0]["message_flow_count"],
                "detected": case_detected,
                "strict_repair_success": case_strict,
                "model_origin": case_entries[0]["model_origin"],
            }

            if args.pipeline_smoke:
                smoke_entries: list[dict] = []
                if case_entries[0]["model_origin"] == "synthetic_scaled_collaboration":
                    seen_types: set[str] = set()
                    for entry in case_entries:
                        if entry["anomaly_type"] not in seen_types:
                            smoke_entries.append(entry)
                            seen_types.add(entry["anomaly_type"])
                else:
                    smoke_entries.append(case_entries[0])
                for entry in smoke_entries:
                    smoke_checked += 1
                    log_path = REPOSITORY_ROOT / entry["log_file"]
                    report = run_analysis(
                        bpmn_path,
                        log_path,
                        REPOSITORY_ROOT
                        / "output"
                        / "extended_benchmark_smoke"
                        / case_name
                        / log_path.stem,
                        write_json=True,
                    )
                    if report.violating_process_count < 1:
                        errors.append(f"{entry['log_file']}: full pipeline found no violation")
                    if not _strict_report_success(report):
                        errors.append(f"{entry['log_file']}: full pipeline strict repair failed")

    payload = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "entry_count": len(entries),
        "checked_entries": checked,
        "case_count": len(by_case),
        "origin_counts": dict(sorted(origin_counts.items())),
        "anomaly_counts": dict(sorted(anomaly_counts.items())),
        "all_detected": all(item["detected"] for item in entries),
        "all_strict_repair_success": all(item["strict_repair_success"] for item in entries),
        "pipeline_smoke_checked": smoke_checked,
        "results_by_case": results_by_case,
        "error_count": len(errors),
        "errors": errors,
    }
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"cases: {len(by_case)}")
    print(f"entries checked: {checked}/{len(entries)}")
    print(f"pipeline smoke: {smoke_checked}")
    print(f"anomaly counts: {dict(sorted(anomaly_counts.items()))}")
    print(f"errors: {len(errors)}")
    print(f"report: {report_path}")
    if errors:
        for error in errors[:20]:
            print(f"  - {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
