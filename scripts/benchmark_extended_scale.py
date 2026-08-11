from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import tempfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pm4py

from chapter7.bpmn import load_process_models
from chapter7.pipeline import run_analysis
from scripts.validate_extended_benchmark import _strict_report_success


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对6个规模递增合成BPMN的全部日志执行预热性能基准。")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "experiments" / "extended_benchmark_manifest.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY_ROOT / "experiments" / "extended_scale_benchmark.json",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=REPOSITORY_ROOT / "experiments" / "extended_scale_benchmark.csv",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
    synthetic = [
        entry
        for entry in manifest["entries"]
        if entry["model_origin"] == "synthetic_scaled_collaboration"
    ]
    cases: dict[str, list[dict]] = {}
    for entry in synthetic:
        cases.setdefault(entry["case"], []).append(entry)

    raw_runs: list[dict] = []
    summaries: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="pais-scale-warmup-") as temp:
        first_entry = sorted(synthetic, key=lambda item: item["case"])[0]
        load_process_models(
            REPOSITORY_ROOT / first_entry["bpmn_file"],
            Path(temp) / "warmup",
        )

        for case_name in sorted(cases, key=lambda name: cases[name][0]["participant_count"]):
            entries = sorted(cases[case_name], key=lambda item: item["log_file"])
            elapsed: list[float] = []
            detected = 0
            strict = 0
            for entry in entries:
                log_path = REPOSITORY_ROOT / entry["log_file"]
                report = run_analysis(
                    REPOSITORY_ROOT / entry["bpmn_file"],
                    log_path,
                    REPOSITORY_ROOT / "output" / "extended_scale_benchmark" / case_name / log_path.stem,
                    write_json=False,
                )
                elapsed.append(report.elapsed_seconds)
                detected += report.violating_process_count > 0
                strict += _strict_report_success(report)
                raw_runs.append(
                    {
                        "case": case_name,
                        "log_file": entry["log_file"],
                        "anomaly_type": entry["anomaly_type"],
                        "participant_count": entry["participant_count"],
                        "message_flow_count": entry["message_flow_count"],
                        "trace_count": entry["normal_trace_count"] + entry["abnormal_trace_count"],
                        "elapsed_seconds": report.elapsed_seconds,
                        "violating_process_count": report.violating_process_count,
                        "strict_repair_success": _strict_report_success(report),
                    }
                )
            summary = {
                "case": case_name,
                "participant_count": entries[0]["participant_count"],
                "message_flow_count": entries[0]["message_flow_count"],
                "run_count": len(entries),
                "mean_seconds": statistics.fmean(elapsed),
                "median_seconds": statistics.median(elapsed),
                "standard_deviation_seconds": statistics.pstdev(elapsed),
                "p95_seconds": _percentile(elapsed, 0.95),
                "min_seconds": min(elapsed),
                "max_seconds": max(elapsed),
                "detected": detected,
                "strict_repair_success": strict,
            }
            summaries.append(summary)
            print(
                f"{case_name}: {len(entries)} runs, mean={summary['mean_seconds']:.4f}s, "
                f"p95={summary['p95_seconds']:.4f}s"
            )

    payload = {
        "schema_version": 1,
        "protocol": {
            "description": "one PM4Py warm-up, then one full analysis for each of 90 synthetic logs",
            "timer": "AnalysisReport.elapsed_seconds",
            "includes": [
                "BPMN normalization",
                "BPMN-to-process-tree conversion",
                "message-pattern analysis",
                "MinAS diagnosis",
                "repair generation and strict verification",
                "input hashing",
            ],
            "excludes": ["Python/PM4Py import", "JSON serialization", "terminal rendering"],
        },
        "environment": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "pm4py": pm4py.__version__,
        },
        "run_count": len(raw_runs),
        "all_detected": all(run["violating_process_count"] > 0 for run in raw_runs),
        "all_strict_repair_success": all(run["strict_repair_success"] for run in raw_runs),
        "summaries": summaries,
        "runs": raw_runs,
    }
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_path = args.csv.resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(raw_runs[0]))
        writer.writeheader()
        writer.writerows(raw_runs)
    print(f"report: {report_path}")
    print(f"csv: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
