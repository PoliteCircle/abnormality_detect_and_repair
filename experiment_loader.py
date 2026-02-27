from __future__ import annotations
from pathlib import Path
from typing import Set, Tuple, List, Optional
import re

from bpmn_transform import transform_bpmn_events_and_gateways

Trace = Tuple[str, ...]


# =========================================================
# 读取 global_log 文件
# =========================================================
def load_global_log(path: Path) -> Set[Trace]:
    """
    读取日志文件：
    每一行是一条消息迹
    格式: M1,M2,M3_s,M3_r

    支持:
        空行
        注释 (# 开头)
    """
    traces: Set[Trace] = set()

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            tokens = tuple(x.strip() for x in line.split(",") if x.strip())
            if len(tokens) == 0:
                continue

            traces.add(tokens)

    return traces


# =========================================================
# 选择实验目录
# =========================================================
def choose_experiment_case(exp_root: Path) -> Path:
    if not exp_root.exists():
        raise RuntimeError(f"'experiments' directory not found: {exp_root}")

    cases: List[Path] = [p for p in exp_root.iterdir() if p.is_dir()]

    if not cases:
        raise RuntimeError("No experiment folders found in /experiments")

    # 稳定排序：按名字
    cases.sort(key=lambda p: p.name)

    print("\nAvailable experiment cases:\n")
    for i, c in enumerate(cases):
        print(f"[{i}] {c.name}")

    while True:
        idx = input("\nSelect case index: ").strip()
        if idx.isdigit() and int(idx) < len(cases):
            return cases[int(idx)]
        print("Invalid index, try again.")


# =========================================================
# 选择 global_log_x 文件
# =========================================================
_GLOBAL_LOG_RE = re.compile(r"^global_log_(\d+)(?:\.txt)?$")

def _extract_global_log_index(p: Path) -> Optional[int]:
    m = _GLOBAL_LOG_RE.match(p.name)
    if not m:
        return None
    return int(m.group(1))

def choose_global_log_file(case_dir: Path) -> Path:
    """
    在 case_dir 下查找所有:
        global_log_0 / global_log_1 / ... （可带 .txt）
    并让用户选择。
    """
    candidates: List[Path] = []
    for p in case_dir.iterdir():
        if p.is_file() and _extract_global_log_index(p) is not None:
            candidates.append(p)

    if not candidates:
        raise RuntimeError(
            f"No global log files found in {case_dir}. "
            f"Expected files like global_log_0(.txt), global_log_1(.txt), ..."
        )

    # 按数字后缀排序
    candidates.sort(key=lambda p: _extract_global_log_index(p) or 0)

    print("\nAvailable global_log files:\n")
    for i, p in enumerate(candidates):
        idx = _extract_global_log_index(p)
        print(f"[{i}] {p.name}  (x={idx})")

    while True:
        s = input("\nSelect global_log index: ").strip()
        if s.isdigit() and int(s) < len(candidates):
            return candidates[int(s)]
        print("Invalid index, try again.")


# =========================================================
# 主加载接口
# =========================================================
def load_experiment(base_dir: Path):
    """
    返回:
        new_bpmn_path: Path
        global_log: Set[Trace]
    """
    exp_root = base_dir / "experiments"
    case_dir = choose_experiment_case(exp_root)

    print(f"\n[INFO] Selected case: {case_dir.name}")

    # --- BPMN ---
    bpmn_path = case_dir / "collaboration.bpmn"
    if not bpmn_path.exists():
        raise RuntimeError(f"Missing file: {bpmn_path}")

    print("[LOAD] BPMN =", bpmn_path)

    # --- LOG (改：选择 global_log_x) ---
    log_file = choose_global_log_file(case_dir)
    print("[LOAD] global_log =", log_file)

    global_log = load_global_log(log_file)

    print("\n[LOAD] global_log traces:")
    for t in sorted(global_log):
        print("   ", t)

    # --- TRANSFORM BPMN ---
    out_dir = base_dir / "generated_bpmn" / case_dir.name
    new_bpmn_path = transform_bpmn_events_and_gateways(
        src_bpmn_path=bpmn_path,
        out_dir=out_dir,
        out_name="collaboration.normalized.bpmn",
    )
    print("[SAVE] normalized BPMN =", new_bpmn_path)

    return new_bpmn_path, global_log