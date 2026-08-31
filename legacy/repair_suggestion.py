























from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ast_expr import Leaf, OpNode

Trace = Tuple[str, ...]
EPS = "ε"





def expr_to_str(e) -> str:
    if isinstance(e, Leaf):
        return e.name
    if isinstance(e, OpNode):
        return f"{e.op}(" + ",".join(expr_to_str(c) for c in e.children) + ")"
    return str(e)





def collect_messages(e) -> Set[str]:




    if isinstance(e, Leaf):
        return set() if e.name == EPS else {e.name}
    if isinstance(e, OpNode):
        s: Set[str] = set()
        for c in e.children:
            s |= collect_messages(c)
        return s
    return set()


def project_trace_to_msgs(tr: Trace, msgset: Set[str]) -> Trace:

    return tuple(x for x in tr if x in msgset)





def can_generate(e, tr: Trace, debug: bool = False, indent: str = "") -> bool:








    if isinstance(e, Leaf):
        if e.name == EPS:
            ok = (len(tr) == 0)
        else:
            ok = (tr == (e.name,))
        if debug:
            print(f"{indent}[GEN] Leaf {e.name} vs tr={tr} -> {ok}")
        return ok

    if not isinstance(e, OpNode):
        if debug:
            print(f"{indent}[GEN] Unknown node {e} -> False")
        return False

    op = e.op
    children = e.children

    if op == "+":
        if debug:
            print(f"{indent}[GEN] Choice + node={expr_to_str(e)} tr={tr}")
        for c in children:
            if can_generate(c, tr, debug=debug, indent=indent + "  "):
                if debug:
                    print(f"{indent}  [GEN] Choice success by {expr_to_str(c)}")
                return True
        if debug:
            print(f"{indent}  [GEN] Choice failed for all branches")
        return False

    if op == ".":
        if debug:
            print(f"{indent}[GEN] Seq . node={expr_to_str(e)} tr={tr}")
        return _seq_generate_left_to_right(children, tr, debug=debug, indent=indent + "  ")

    if op == "|":
        if debug:
            print(f"{indent}[GEN] Par | node={expr_to_str(e)} tr={tr}")

        all_msgs: Set[str] = set()
        for c in children:
            all_msgs |= collect_messages(c)
        if set(tr) != all_msgs:
            if debug:
                print(f"{indent}  [GEN] Par failed: set(tr)={set(tr)} != all_msgs={all_msgs}")
            return False

        for c in children:
            cm = collect_messages(c)
            sub_tr = project_trace_to_msgs(tr, cm)
            ok = can_generate(c, sub_tr, debug=debug, indent=indent + "  ")
            if not ok:
                if debug:
                    print(f"{indent}  [GEN] Par failed at child={expr_to_str(c)} sub_tr={sub_tr}")
                return False

        if debug:
            print(f"{indent}  [GEN] Par success")
        return True

    if debug:
        print(f"{indent}[GEN] Unsupported op='{op}' -> False")
    return False


def _seq_generate_left_to_right(children: Sequence, tr: Trace, debug: bool, indent: str) -> bool:





    def dfs(i: int, pos: int) -> bool:
        if i == len(children):
            return pos == len(tr)

        child = children[i]

        for L in range(0, len(tr) - pos + 1):
            seg = tr[pos:pos + L]
            if debug:
                print(f"{indent}[SEQ] try child#{i}={expr_to_str(child)} seg={seg}")

            if can_generate(child, seg, debug=debug, indent=indent + "  "):
                if debug:
                    print(f"{indent}[SEQ] child#{i} success, move on (pos {pos}->{pos+L})")
                if dfs(i + 1, pos + L):
                    return True
                else:
                    if debug:
                        print(f"{indent}[SEQ] backtrack after child#{i}")
        return False

    return dfs(0, 0)





@dataclass
class RepairSuggestion:
    reason: str
    before_subtree: str
    after_subtree: str


    before_pass_normals: int
    before_total_normals: int
    before_pass_abnormals: int
    before_total_abnormals: int

    after_pass_normals: int
    after_total_normals: int
    after_pass_abnormals: int
    after_total_abnormals: int


    ok_on_normals: bool
    ok_on_abnormals: bool

    detail: List[str]





def suggest_repairs_for_minas_subtree(
    minas_subtree,
    normal_traces_proj: Set[Trace],
    abnormal_traces_proj: Set[Trace],
    debug: bool = True
) -> List[RepairSuggestion]:









    before_str = expr_to_str(minas_subtree)

    msgset = collect_messages(minas_subtree)
    normals = {project_trace_to_msgs(tr, msgset) for tr in normal_traces_proj}
    abnormals = {project_trace_to_msgs(tr, msgset) for tr in abnormal_traces_proj}

    if debug:
        print("\n" + "-" * 80)
        print(f"[REPAIR] MinAS subtree = {before_str}")
        print(f"[REPAIR] msgset = {sorted(msgset)}")
        print(f"[REPAIR] normals_on_subtree = {sorted(normals)}")
        print(f"[REPAIR] abnormals_on_subtree = {sorted(abnormals)}")

    candidates: List[Tuple[str, object]] = []


    if isinstance(minas_subtree, Leaf) and minas_subtree.name != EPS:
        a = minas_subtree.name
        if () in abnormals:
            candidates.append((
                "规则说明：异常包含空序列，说明该消息可能未发送/未接收；可将其删除为 τ(ε)。",
                Leaf(EPS)
            ))
            candidates.append((
                "规则说明：有时空有时包含该消息，说明该消息可能处于未暴露的排他/事件分支；可改成 +(a, ε) 使其可选。",
                OpNode("+", [Leaf(a), Leaf(EPS)])
            ))


    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "." and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "规则说明：整个顺序块在异常里为空，说明这段可能不发生；可将该顺序结构整体替换为 τ(ε)。",
                Leaf(EPS)
            ))

        all_obs = set(normals) | set(abnormals)
        two_len = [t for t in all_obs if len(t) == 2]
        found_swap = any(((t[1], t[0]) in all_obs and (t[1], t[0]) != t) for t in two_len)
        if found_swap:
            candidates.append((
                "规则说明：观察到同一对消息既有(a,b)也有(b,a)，说明可能存在未暴露并行；可将 '.' 替换为 '|'。",
                OpNode("|", list(minas_subtree.children))
            ))

        for nt in normals:
            if len(nt) == 2:
                rev = (nt[1], nt[0])
                if rev in abnormals:
                    if len(minas_subtree.children) == 2:
                        c0, c1 = minas_subtree.children
                        candidates.append((
                            "规则说明：正常为(a,b)但异常出现(b,a)，可能是顺序写反；可交换两个子结构顺序。",
                            OpNode(".", [c1, c0])
                        ))
                    break


    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "+" and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "规则说明：排他/事件网关在异常里为空，说明该结构可能不发生；可将该网关整体替换为 τ(ε)。",
                Leaf(EPS)
            ))

        all_obs = set(normals) | set(abnormals)
        two_len = [t for t in all_obs if len(t) == 2]
        found_swap = any(((t[1], t[0]) in all_obs and (t[1], t[0]) != t) for t in two_len)
        if found_swap:
            candidates.append((
                "规则说明：排他结构却观察到两消息可交错/反序，可能应为并行；可将 '+' 替换为 '|'。",
                OpNode("|", list(minas_subtree.children))
            ))


    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "|" and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "规则说明：并行结构在异常里为空，说明该结构可能不发生；可整体替换为 τ(ε)。",
                Leaf(EPS)
            ))

        par_msgs = set()
        all_leaf = True
        for ch in minas_subtree.children:
            if isinstance(ch, Leaf):
                par_msgs.add(ch.name)
            else:
                all_leaf = False
                break

        if not all_leaf or len(par_msgs) < 2:
            if debug:
                print("  [SKIP] Rule(12): parallel subtree not all-leaf or msgset<2, skip this heuristic")
        else:
            observed = set(normals) | set(abnormals)
            has_all_msgs_trace = any(set(t) == par_msgs for t in observed)
            has_single_branch_trace = any((len(t) == 1 and next(iter(t)) in par_msgs) for t in observed)

            if (not has_all_msgs_trace) and has_single_branch_trace:
                candidates.append((
                    "规则说明：原本并行但观测中只出现其中一条消息，且从未观测到两条同时出现；可能应为排他/事件网关；可将 '|' 替换为 '+'。",
                    OpNode("+", list(minas_subtree.children))
                ))
            else:
                if debug:
                    print(f"  [SKIP] Rule(12): has_all_msgs_trace={has_all_msgs_trace}, "
                          f"has_single_branch_trace={has_single_branch_trace}, par_msgs={sorted(par_msgs)}")




    suggestions: List[RepairSuggestion] = []
    for reason, after in candidates:
        after_str = expr_to_str(after)
        detail: List[str] = []

        beforeN_pass = 0
        beforeA_pass = 0
        afterN_pass = 0
        afterA_pass = 0

        if debug:
            print("\n" + "  " + "-" * 76)
            print(f"  [CAND] reason = {reason}")
            print(f"  [CAND] before = {before_str}")
            print(f"  [CAND] after  = {after_str}")


        for tr in sorted(normals):
            ok_before = can_generate(minas_subtree, tr, debug=debug, indent="    ")
            ok_after = can_generate(after, tr, debug=debug, indent="    ")

            beforeN_pass += int(ok_before)
            afterN_pass += int(ok_after)
            detail.append(f"[NORMAL] tr={tr} before={ok_before} after={ok_after}")


        for tr in sorted(abnormals):
            ok_before = can_generate(minas_subtree, tr, debug=debug, indent="    ")
            ok_after = can_generate(after, tr, debug=debug, indent="    ")

            beforeA_pass += int(ok_before)
            afterA_pass += int(ok_after)
            detail.append(f"[ABNOR] tr={tr} before={ok_before} after={ok_after}")

        totalN = len(normals)
        totalA = len(abnormals)

        okN = (afterN_pass == totalN)
        okA = (afterA_pass == totalA)

        if debug:
            print(f"  [STAT] NORMAL: {beforeN_pass}/{totalN} -> {afterN_pass}/{totalN}")
            print(f"  [STAT] ABNOR : {beforeA_pass}/{totalA} -> {afterA_pass}/{totalA}")

        suggestions.append(RepairSuggestion(
            reason=reason,
            before_subtree=before_str,
            after_subtree=after_str,

            before_pass_normals=beforeN_pass,
            before_total_normals=totalN,
            before_pass_abnormals=beforeA_pass,
            before_total_abnormals=totalA,

            after_pass_normals=afterN_pass,
            after_total_normals=totalN,
            after_pass_abnormals=afterA_pass,
            after_total_abnormals=totalA,

            ok_on_normals=okN,
            ok_on_abnormals=okA,
            detail=detail
        ))


    suggestions.sort(
        key=lambda s: (
            not (s.ok_on_normals and s.ok_on_abnormals),
            -(s.after_pass_abnormals - s.before_pass_abnormals),
            s.after_subtree
        )
    )

    if debug:
        print("\n" + "-" * 80)
        print(f"[REPAIR] candidates={len(suggestions)} (sorted best-first)")
        for s in suggestions:
            print(
                f"  - after={s.after_subtree} "
                f"N:{s.before_pass_normals}/{s.before_total_normals}->{s.after_pass_normals}/{s.after_total_normals} "
                f"A:{s.before_pass_abnormals}/{s.before_total_abnormals}->{s.after_pass_abnormals}/{s.after_total_abnormals} "
                f"okN={s.ok_on_normals} okA={s.ok_on_abnormals}"
            )
        print("-" * 80)

    return suggestions


def suggest_repairs_for_process(
    process_message_tree,
    minas_subtrees: List,
    normal_traces_proj: Set[Trace],
    abnormal_traces_proj: Set[Trace],
    debug: bool = True
) -> Dict[str, List[RepairSuggestion]]:




    if debug:
        print("\n" + "=" * 80)
        print(f"[REPAIR] process_message_tree = {expr_to_str(process_message_tree)}")
        print(f"[REPAIR] normal_traces_proj size={len(normal_traces_proj)} sample={sorted(normal_traces_proj)[:20]}")
        print(f"[REPAIR] abnormal_traces_proj size={len(abnormal_traces_proj)} sample={sorted(abnormal_traces_proj)[:20]}")

    out: Dict[str, List[RepairSuggestion]] = {}
    for st in minas_subtrees:
        k = expr_to_str(st)
        out[k] = suggest_repairs_for_minas_subtree(
            st,
            normal_traces_proj=normal_traces_proj,
            abnormal_traces_proj=abnormal_traces_proj,
            debug=debug
        )
    return out
