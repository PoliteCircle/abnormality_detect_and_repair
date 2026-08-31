from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .logs import (
    discover_bpmn_files,
    discover_cases,
    discover_log_files,
    resolve_case_input,
)
from .pipeline import run_analysis
from .reporting import print_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按论文第7章检测潜在异常子流程、求解 MinAS 并生成潜在修复方案。",
    )
    parser.add_argument("--list", action="store_true", help="列出 experiments 下所有可选 BPMN 与日志")
    parser.add_argument("--case", help="experiments 下的案例目录名；省略全部输入参数时进入交互选择")
    parser.add_argument("--bpmn-name", help="案例目录中的 BPMN 文件名")
    parser.add_argument("--log-file", help="案例目录中的 global_log_*.txt 文件名")
    parser.add_argument("--bpmn", type=Path, help="直接指定 BPMN 路径（必须同时指定 --log）")
    parser.add_argument("--log", type=Path, help="直接指定日志路径（必须同时指定 --bpmn）")
    parser.add_argument("--experiments-dir", type=Path, help="实验根目录，默认是仓库的 experiments")
    parser.add_argument("--output-dir", type=Path, help="运行时文件和 JSON 报告目录")
    parser.add_argument("--report-name", default="analysis-report.json", help="JSON 报告文件名")
    parser.add_argument("--pattern-limit", type=int, default=10_000, help="每类展示消息模式的上限")
    parser.add_argument("--behavior-limit", type=int, default=20_000, help="验证 Definition 7.17 的枚举上限")
    parser.add_argument("--summary", action="store_true", help="隐藏逐节点预分割细节")
    parser.add_argument("--no-json", action="store_true", help="不写 JSON 详细报告")
    return parser


def _list_inputs(experiments_dir: Path) -> None:
    print(f"experiments = {experiments_dir.resolve()}")
    for case in discover_cases(experiments_dir):
        print(f"\n[{case.name}]")
        bpmns = discover_bpmn_files(case)
        logs = discover_log_files(case)
        print("  BPMN:")
        for path in bpmns:
            print(f"    - {path.name}")
        print("  LOG:")
        for path in logs:
            print(f"    - {path.name}")
        if not logs:
            print("    - （无日志，不能运行分析）")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    experiments_dir = (args.experiments_dir or repository / "experiments").resolve()

    try:
        if args.list:
            _list_inputs(experiments_dir)
            return 0

        direct = args.bpmn is not None or args.log is not None
        if direct:
            if args.bpmn is None or args.log is None:
                parser.error("--bpmn 与 --log 必须同时指定")
            bpmn_path = args.bpmn.resolve()
            log_path = args.log.resolve()
            case_name = bpmn_path.parent.name
        else:
            interactive = args.case is None
            selected = resolve_case_input(
                experiments_dir,
                case_name=args.case,
                bpmn_name=args.bpmn_name,
                log_name=args.log_file,
                interactive=interactive,
            )
            bpmn_path = selected.bpmn_path
            log_path = selected.log_path
            case_name = selected.case_name

        if not bpmn_path.is_file():
            raise FileNotFoundError(f"BPMN file does not exist: {bpmn_path}")
        if not log_path.is_file():
            raise FileNotFoundError(f"log file does not exist: {log_path}")
        if args.pattern_limit < 1 or args.behavior_limit < 1:
            raise ValueError("pattern/behavior limit must be positive")

        output_dir = (
            args.output_dir
            or repository / "output" / case_name / log_path.stem
        ).resolve()
        print("\n即将使用以下输入：")
        print(f"  BPMN = {bpmn_path}")
        print(f"  LOG  = {log_path}")
        print(f"  OUT  = {output_dir}")

        report = run_analysis(
            bpmn_path,
            log_path,
            output_dir,
            pattern_limit=args.pattern_limit,
            behavior_limit=args.behavior_limit,
            report_name=args.report_name,
            write_json=not args.no_json,
        )
        print_report(report, detailed=not args.summary)
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 2
