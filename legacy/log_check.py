from __future__ import annotations
from dataclasses import dataclass
from typing import Set, Tuple, List, Optional

from bpmn_subprocess import SubprocessInfo

Trace = Tuple[str, ...]


@dataclass
class ViolationRecord:
    process_id: str
    participant_name: str
    bad_global_traces: List[Tuple[Trace, Trace]]



def _split_sr(token: str) -> Tuple[str, Optional[str]]:






    if token.endswith("_s") and len(token) > 2:
        return token[:-2], "s"
    if token.endswith("_r") and len(token) > 2:
        return token[:-2], "r"
    return token, None


def project_trace_with_sr(global_trace: Trace, MEo: Set[str], MEi: Set[str], debug: bool = False) -> Trace:






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
