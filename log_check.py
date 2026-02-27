from __future__ import annotations
from dataclasses import dataclass
from typing import Set, Tuple, List, Optional

from bpmn_subprocess import SubprocessInfo

Trace = Tuple[str, ...]  # 一条消息迹：消息名序列，例如 ("M1","M2")


@dataclass
class ViolationRecord:
    process_id: str
    participant_name: str
    bad_global_traces: List[Tuple[Trace, Trace]]
    # (global_trace, projected_trace)


def _split_sr(token: str) -> Tuple[str, Optional[str]]:
    """
    将全局日志 token 解析为 (base, kind)
    - "M1_s" -> ("M1", "s")
    - "M1_r" -> ("M1", "r")
    - "M1"   -> ("M1", None)
    """
    if token.endswith("_s") and len(token) > 2:
        return token[:-2], "s"
    if token.endswith("_r") and len(token) > 2:
        return token[:-2], "r"
    return token, None


def project_trace_with_sr(global_trace: Trace, MEo: Set[str], MEi: Set[str], debug: bool = False) -> Trace:
    """
    投影（带 send/recv 语义）：
    - 全局 token 为 "Mi_s"：只对子流程“发送集合 MEo”可见，投影为 "Mi"
    - 全局 token 为 "Mi_r"：只对子流程“接收集合 MEi”可见，投影为 "Mi"
    - 若全局 token 无后缀（兼容旧日志）：只要在 (MEo∪MEi) 内就可见，投影为自身
    """
    out: List[str] = []

    visible_base = set(MEo) | set(MEi)

    for tok in global_trace:
        base, kind = _split_sr(tok)

        keep = False
        if kind == "s":
            keep = base in MEo
        elif kind == "r":
            keep = base in MEi
        else:
            # 兼容旧日志：没有 _s/_r 时，按“可见消息集合”判断
            keep = base in visible_base

        if debug:
            role = "SEND" if kind == "s" else ("RECV" if kind == "r" else "RAW")
            print(f"          [PROJ] tok={tok:>8} -> base={base:>6} kind={role:>4} "
                  f"keep={keep} (MEo={base in MEo}, MEi={base in MEi})")

        if keep:
            out.append(base)

    return tuple(out)


def find_violating_processes(
    infos: List[SubprocessInfo],
    global_log: Set[Trace],
    ignore_empty_projection: bool = False,
    debug: bool = True
) -> List[ViolationRecord]:
    """
    对每个 process：
      visible = MEo ∪ MEi（但投影时要区分 _s/_r）
      对每条全局迹 t：
        pt = π_process(t)
        若 pt 不在 pattern，则记录反例
    """
    print("=" * 80)
    print(f"[CHECK] global_log_size={len(global_log)}")

    violations: List[ViolationRecord] = []

    for s in infos:
        MEo = set(s.MEo)
        MEi = set(s.MEi)
        visible = MEo | MEi

        print("-" * 80)
        print(f"[CHECK] participant='{s.participant_name}' process_id='{s.process_id}'")
        print(f"        MEo(send)={sorted(MEo)}")
        print(f"        MEi(recv)={sorted(MEi)}")
        print(f"        visible_base_msgs={sorted(visible)}")
        print(f"        pattern_size={len(s.pattern)}")

        bad: List[Tuple[Trace, Trace]] = []

        for t in global_log:
            # 关键：按 _s/_r 决定可见性；投影结果去掉后缀回到 base 名，从而能匹配 pattern
            pt = project_trace_with_sr(t, MEo, MEi, debug=False)

            if ignore_empty_projection and len(pt) == 0:
                if debug:
                    print(f"        trace={t} -> projected=() -> SKIP(empty)")
                continue

            ok = (pt in s.pattern)

            if debug:
                print(f"        trace={t} -> projected={pt} -> {'OK' if ok else 'VIOLATION'}")

            if not ok:
                bad.append((t, pt))

        if bad:
            violations.append(ViolationRecord(
                process_id=s.process_id,
                participant_name=s.participant_name,
                bad_global_traces=bad
            ))

    print("=" * 80)
    print(f"[CHECK] violating_processes={len(violations)}")
    return violations