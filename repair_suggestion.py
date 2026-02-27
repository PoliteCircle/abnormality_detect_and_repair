# repair_suggestion.py
# -*- coding: utf-8 -*-
"""
根据“潜在修复方案”（表 6-5 的思想），对每个异常子流程的 MinAS 子树提出候选修复，
并用可生成性（match/generate）检查保证：
  - 正常投影日志仍可通过
  - 异常投影日志也可通过

修改点（你这次的需求）：
- 不再只输出 okN/okA（是否覆盖全部），而是输出：
    修复前：能通过的 normal/abnormal trace 数量
    修复后：能通过的 normal/abnormal trace 数量
  同时保留 ok_on_normals/ok_on_abnormals 方便排序。

注意：
- 本文件复用你工程里的 ast_expr.Leaf / ast_expr.OpNode，不再自造 Expr 基类，更不会 subclass typing.Union。
- 约定：
  '.' = 顺序
  '+' = 选择（排他/事件网关在流程树里你都映射成 '+'）
  '|' = 并行（交错语义）
- 我们引入一个特殊叶子 EPS="ε" 表示 τ/空动作，只生成空 trace ()。
  修复建议里出现 ε 时，仅用于“匹配检查”和“展示建议”，不要求你把它写回 BPMN。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ast_expr import Leaf, OpNode  # 复用你的 AST 类型

Trace = Tuple[str, ...]
EPS = "ε"  # 空动作/τ：只生成空 trace ()


# -----------------------------
# 工具：AST -> 字符串（用于输出）
# -----------------------------
def expr_to_str(e) -> str:
    if isinstance(e, Leaf):
        return e.name
    if isinstance(e, OpNode):
        return f"{e.op}(" + ",".join(expr_to_str(c) for c in e.children) + ")"
    return str(e)


# -----------------------------
# 工具：收集消息叶子集合
# -----------------------------
def collect_messages(e) -> Set[str]:
    """
    收集该子树的“消息名”叶子集合。
    约定 EPS(ε) 不算消息。
    """
    if isinstance(e, Leaf):
        return set() if e.name == EPS else {e.name}
    if isinstance(e, OpNode):
        s: Set[str] = set()
        for c in e.children:
            s |= collect_messages(c)
        return s
    return set()


def project_trace_to_msgs(tr: Trace, msgset: Set[str]) -> Trace:
    """把 trace 投影到给定 msgset 上（按原顺序过滤）"""
    return tuple(x for x in tr if x in msgset)


# -----------------------------
# 可生成性检查（用于验证修复候选）
# -----------------------------
def can_generate(e, tr: Trace, debug: bool = False, indent: str = "") -> bool:
    """
    判断 e 是否“能生成”该消息序列 tr。
    这是一个“结构化匹配/预分割”式的判定器（用于 debug/验证修复候选），不是完整语义模型检查。

    假设（与你论文限制一致）：
    - 结构化、无循环
    - 每个消息活动最多出现一次（即消息名在树上唯一），这样并行用投影检查足够靠谱
    """
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

    if op == "+":  # 选择：任一分支能生成即可
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

    if op == ".":  # 顺序：必须按顺序依次匹配
        if debug:
            print(f"{indent}[GEN] Seq . node={expr_to_str(e)} tr={tr}")
        return _seq_generate_left_to_right(children, tr, debug=debug, indent=indent + "  ")

    if op == "|":  # 并行：交错语义（用投影近似判断）
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
    """
    顺序结构匹配：必须先匹配第一个子结构，再继续匹配后续。
    这里做“前缀切分”的回溯，但确保是从左到右推进。
    """

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


# -----------------------------
# 修复建议的数据结构（改为统计）
# -----------------------------
@dataclass
class RepairSuggestion:
    reason: str
    before_subtree: str
    after_subtree: str

    # 修复前/后通过条数统计
    before_pass_normals: int
    before_total_normals: int
    before_pass_abnormals: int
    before_total_abnormals: int

    after_pass_normals: int
    after_total_normals: int
    after_pass_abnormals: int
    after_total_abnormals: int

    # 是否覆盖全部（用于排序/筛选）
    ok_on_normals: bool
    ok_on_abnormals: bool

    detail: List[str]


# -----------------------------
# 生成候选修复（核心）
# -----------------------------
def suggest_repairs_for_minas_subtree(
    minas_subtree,
    normal_traces_proj: Set[Trace],
    abnormal_traces_proj: Set[Trace],
    debug: bool = True
) -> List[RepairSuggestion]:
    """
    对单个 MinAS 子树给出候选修复方案，并验证：
      normal_traces_proj ⊆ L(after_subtree) 且 abnormal_traces_proj ⊆ L(after_subtree)

    normal_traces_proj / abnormal_traces_proj:
      已经是投影到“当前子流程消息集合”上的 trace；
      本函数内部会再投影到 minas_subtree 的消息集合上。
    """

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

    # ---------- 规则族 A：单节点（叶子）异常 ----------
    if isinstance(minas_subtree, Leaf) and minas_subtree.name != EPS:
        a = minas_subtree.name
        if () in abnormals:
            candidates.append((
                "表6-5(1/3/7/10)直觉：异常包含空序列，说明该消息可能未发送/未接收；可将其删除为 τ(ε)。",
                Leaf(EPS)
            ))
            candidates.append((
                "表6-5(2)直觉：有时空有时包含该消息，说明该消息可能处于未暴露的排他/事件分支；可改成 +(a, ε) 使其可选。",
                OpNode("+", [Leaf(a), Leaf(EPS)])
            ))

    # ---------- 规则族 B：顺序结构异常 ----------
    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "." and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "表6-5(3)直觉：整个顺序块在异常里为空，说明这段可能不发生；可将该顺序结构整体替换为 τ(ε)。",
                Leaf(EPS)
            ))

        all_obs = set(normals) | set(abnormals)
        two_len = [t for t in all_obs if len(t) == 2]
        found_swap = any(((t[1], t[0]) in all_obs and (t[1], t[0]) != t) for t in two_len)
        if found_swap:
            candidates.append((
                "表6-5(5)直觉：观察到同一对消息既有(a,b)也有(b,a)，说明可能存在未暴露并行；可将 '.' 替换为 '|'。",
                OpNode("|", list(minas_subtree.children))
            ))

        for nt in normals:
            if len(nt) == 2:
                rev = (nt[1], nt[0])
                if rev in abnormals:
                    if len(minas_subtree.children) == 2:
                        c0, c1 = minas_subtree.children
                        candidates.append((
                            "表6-5(4)直觉：正常为(a,b)但异常出现(b,a)，可能是顺序写反；可交换两个子结构顺序。",
                            OpNode(".", [c1, c0])
                        ))
                    break

    # ---------- 规则族 C：排他/事件网关异常（'+'） ----------
    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "+" and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "表6-5(7)直觉：排他/事件网关在异常里为空，说明该结构可能不发生；可将该网关整体替换为 τ(ε)。",
                Leaf(EPS)
            ))

        all_obs = set(normals) | set(abnormals)
        two_len = [t for t in all_obs if len(t) == 2]
        found_swap = any(((t[1], t[0]) in all_obs and (t[1], t[0]) != t) for t in two_len)
        if found_swap:
            candidates.append((
                "表6-5(8)直觉：排他结构却观察到两消息可交错/反序，可能应为并行；可将 '+' 替换为 '|'。",
                OpNode("|", list(minas_subtree.children))
            ))

    # ---------- 规则族 D：并行网关异常（'|'） ----------
    if isinstance(minas_subtree, OpNode) and minas_subtree.op == "|" and len(minas_subtree.children) >= 2:
        if abnormals == {()}:
            candidates.append((
                "表6-5(10)直觉：并行结构在异常里为空，说明该结构可能不发生；可整体替换为 τ(ε)。",
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
                    "表6-5(12)直觉：原本并行但观测中只出现其中一条消息，且从未观测到两条同时出现；可能应为排他/事件网关；可将 '|' 替换为 '+'。",
                    OpNode("+", list(minas_subtree.children))
                ))
            else:
                if debug:
                    print(f"  [SKIP] Rule(12): has_all_msgs_trace={has_all_msgs_trace}, "
                          f"has_single_branch_trace={has_single_branch_trace}, par_msgs={sorted(par_msgs)}")

    # ---------------------------------
    # 对每个候选做“覆盖验证 + 统计”
    # ---------------------------------
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

        # normal：修复前/后分别统计
        for tr in sorted(normals):
            ok_before = can_generate(minas_subtree, tr, debug=debug, indent="    ")
            ok_after = can_generate(after, tr, debug=debug, indent="    ")

            beforeN_pass += int(ok_before)
            afterN_pass += int(ok_after)
            detail.append(f"[NORMAL] tr={tr} before={ok_before} after={ok_after}")

        # abnormal：修复前/后分别统计
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

    # 把“同时覆盖 normal+abnormal”的排前面；其次可按异常提升幅度排序（可选）
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
    """
    对一个异常子流程：对每个 MinAS 子树输出一组修复建议。
    返回 dict：key=MinAS子树字符串，value=该子树的 RepairSuggestion 列表
    """
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