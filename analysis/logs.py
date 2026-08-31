from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from .model import Trace

LOG_RE = re.compile(r"^global_log_(\d+)(?:\.txt)?$")


@dataclass(frozen=True, slots=True)
class LogTrace:
    line_number: int
    tokens: Trace


@dataclass(frozen=True, slots=True)
class SelectedInput:
    case_name: str
    case_dir: Path
    bpmn_path: Path
    log_path: Path


def discover_cases(experiments_dir: Path) -> list[Path]:
    if not experiments_dir.is_dir():
        raise FileNotFoundError(f"experiments directory does not exist: {experiments_dir}")
    return sorted((path for path in experiments_dir.iterdir() if path.is_dir()), key=lambda path: path.name)


def discover_bpmn_files(case_dir: Path) -> list[Path]:
    return sorted(case_dir.glob("*.bpmn"), key=lambda path: path.name)


def _log_sort_key(path: Path) -> tuple[int, str]:
    match = LOG_RE.match(path.name)
    return (int(match.group(1)), path.name) if match else (10**9, path.name)


def discover_log_files(case_dir: Path) -> list[Path]:
    return sorted(
        (path for path in case_dir.iterdir() if path.is_file() and LOG_RE.match(path.name)),
        key=_log_sort_key,
    )


def load_global_log(path: Path) -> list[LogTrace]:
    traces: list[LogTrace] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = tuple(part.strip() for part in line.split(",") if part.strip())
            if not tokens:
                continue
            traces.append(LogTrace(line_number, tokens))
    if not traces:
        raise ValueError(f"log file contains no traces: {path}")
    return traces


def _split_directional_token(
    token: str,
    sends: frozenset[str],
    receives: frozenset[str],
) -> tuple[str, str | None]:






    if token.endswith("_s") and token[:-2] in sends | receives:
        return token[:-2], "send"
    if token.endswith("_r") and token[:-2] in sends | receives:
        return token[:-2], "receive"
    return token, None


def project_global_trace(
    trace: Trace,
    sends: frozenset[str],
    receives: frozenset[str],
) -> Trace:







    visible = sends | receives
    projected: list[str] = []
    for token in trace:
        base, direction = _split_directional_token(token, sends, receives)
        if direction == "send" and base in sends:
            projected.append(base)
        elif direction == "receive" and base in receives:
            projected.append(base)
        elif direction is None and base in visible:
            projected.append(base)
    return tuple(projected)


def choose_from_console(title: str, choices: list[Path]) -> Path:
    if not choices:
        raise ValueError(f"no choices available for {title}")
    print(f"\n{title}")
    for index, choice in enumerate(choices, start=1):
        print(f"  [{index}] {choice.name}")
    while True:
        try:
            raw = input("请输入序号: ").strip()
        except EOFError as exc:
            raise RuntimeError("标准输入已关闭，请使用 --case/--log-file 指定输入") from exc
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print(f"请输入 1 到 {len(choices)} 之间的整数。")


def resolve_case_input(
    experiments_dir: Path,
    *,
    case_name: str | None = None,
    bpmn_name: str | None = None,
    log_name: str | None = None,
    interactive: bool = False,
) -> SelectedInput:
    cases = discover_cases(experiments_dir)
    if case_name:
        matches = [path for path in cases if path.name == case_name]
        if not matches:
            raise ValueError(
                f"unknown experiment case {case_name!r}; available: "
                + ", ".join(path.name for path in cases)
            )
        case_dir = matches[0]
    elif interactive:
        case_dir = choose_from_console("可用实验案例：", cases)
    else:
        raise ValueError("请使用 --case 指定实验，或不带参数进入交互选择")

    bpmn_files = discover_bpmn_files(case_dir)
    log_files = discover_log_files(case_dir)
    if not bpmn_files:
        raise ValueError(f"experiment contains no BPMN file: {case_dir}")
    if not log_files:
        raise ValueError(f"experiment contains no global_log_*.txt file: {case_dir}")

    if bpmn_name:
        bpmn_path = case_dir / bpmn_name
        if bpmn_path not in bpmn_files:
            raise ValueError(f"BPMN file is not available in {case_dir}: {bpmn_name}")
    elif len(bpmn_files) == 1:
        bpmn_path = bpmn_files[0]
    elif interactive:
        bpmn_path = choose_from_console("请选择 BPMN 文件：", bpmn_files)
    else:
        raise ValueError("该实验有多个 BPMN 文件，请使用 --bpmn-name 指定")

    if log_name:
        log_path = case_dir / log_name
        if log_path not in log_files:
            raise ValueError(f"log file is not available in {case_dir}: {log_name}")
    elif interactive:
        log_path = choose_from_console("请选择日志文件：", log_files)
    elif len(log_files) == 1:
        log_path = log_files[0]
    else:
        raise ValueError("该实验有多个日志文件，请使用 --log-file 指定")

    return SelectedInput(case_dir.name, case_dir, bpmn_path, log_path)
