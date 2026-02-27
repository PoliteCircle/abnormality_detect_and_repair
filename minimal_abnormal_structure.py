from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Union, Optional

from ast_expr import Expr, Leaf, OpNode

Trace = Tuple[str, ...]  # e.g. ("M1","M2")


# =============================================================================
# Expr 在你的工程里是 typing.Union[Leaf, OpNode]，不能被继承。
# 因此 Empty 不继承 Expr。
# =============================================================================

@dataclass(frozen=True)
class Empty:
    """ε：空结构（不包含任何消息）"""
    pass


EPS = Empty()
AnyExpr = Union[Expr, Empty]  # Leaf | OpNode | Empty


def is_empty(e: AnyExpr) -> bool:
    return isinstance(e, Empty)


# =============================================================================
# Debug print
# =============================================================================

def expr_to_str(e: AnyExpr) -> str:
    if isinstance(e, Empty):
        return "ε"
    if isinstance(e, Leaf):
        return e.name
    # assert isinstance(e, OpNode)
    return f"{e.op}(" + ",".join(expr_to_str(c) for c in e.children) + ")"


# =============================================================================
# Trace filter: keep only messages
# =============================================================================

def filter_trace_to_messages(tr: Trace, msg_set: Set[str]) -> Trace:
    return tuple(x for x in tr if x in msg_set)


# =============================================================================
# Simplify tree to messages only
# 规则（按你要求）：
# - '.' 顺序：删除完全没有消息的子树（ε 子段删掉）
# - '+' 选择：所有无消息子树合并为一个 ε（保留“可走空分支”语义）
# - '|' 并行：删除空段（ε 子段删掉）
# =============================================================================

@dataclass(frozen=True)
class ChoiceWithEps:
    """用于表示 +( ..., ε )，因为 OpNode.children 里不能放 Empty"""
    children: Tuple[AnyExpr, ...]

    @property
    def op(self) -> str:
        return "+"


def simplify_tree_to_messages(root: Expr, msg_set: Set[str], debug: bool = False) -> AnyExpr:
    def rec(e: Expr) -> AnyExpr:
        if isinstance(e, Leaf):
            return e if e.name in msg_set else EPS

        assert isinstance(e, OpNode)
        op = e.op
        kids: List[AnyExpr] = [rec(c) for c in e.children]

        if op == ".":
            kids = [c for c in kids if not is_empty(c)]
            if len(kids) == 0:
                return EPS
            if len(kids) == 1:
                return kids[0]
            return OpNode(".", tuple(kids))

        if op == "|":
            kids = [c for c in kids if not is_empty(c)]
            if len(kids) == 0:
                return EPS
            if len(kids) == 1:
                return kids[0]
            return OpNode("|", tuple(kids))

        if op == "+":
            has_eps = any(is_empty(c) for c in kids)
            non_eps = [c for c in kids if not is_empty(c)]

            # 去重（避免重复分支）
            uniq: Dict[str, AnyExpr] = {}
            for c in non_eps:
                uniq[expr_to_str(c)] = c
            non_eps = list(uniq.values())

            if has_eps:
                packed = tuple(non_eps + [EPS])
                if len(packed) == 1:
                    return packed[0]
                return ChoiceWithEps(packed)

            if len(non_eps) == 0:
                return EPS
            if len(non_eps) == 1:
                return non_eps[0]
            return OpNode("+", tuple(non_eps))

        # 其他 op：保守删 ε
        kids = [c for c in kids if not is_empty(c)]
        if len(kids) == 0:
            return EPS
        if len(kids) == 1:
            return kids[0]
        return OpNode(op, tuple(kids))

    simplified = rec(root)
    if debug:
        print("[SIMPLIFY] msg_set =", sorted(msg_set))
        print("[SIMPLIFY] before  =", expr_to_str(root))
        print("[SIMPLIFY] after   =", expr_to_str(simplified))
    return simplified


# =============================================================================
# NM map: node -> message set
# =============================================================================

def build_nm_map(root: AnyExpr) -> Dict[int, Set[str]]:
    nm_map: Dict[int, Set[str]] = {}

    def rec(e: AnyExpr) -> Set[str]:
        if isinstance(e, Empty):
            nm_map[id(e)] = set()
            return set()
        if isinstance(e, Leaf):
            s = {e.name}
            nm_map[id(e)] = s
            return s

        if isinstance(e, ChoiceWithEps):
            u: Set[str] = set()
            for c in e.children:
                u |= rec(c)
            nm_map[id(e)] = u
            return u

        assert isinstance(e, OpNode)
        u: Set[str] = set()
        for c in e.children:
            u |= rec(c)
        nm_map[id(e)] = u
        return u

    rec(root)
    return nm_map


def te_of_trace(tr: Trace) -> Set[str]:
    return set(tr)


# =============================================================================
# PreSplit result
# =============================================================================

@dataclass
class PreSplit:
    success: bool
    pairs: List[Tuple[AnyExpr, Trace]]
    fail_nodes: List[AnyExpr]
    reason: str = ""


# =============================================================================
# Best parallel assignment allowing empty branches (for diagnosis)
# =============================================================================

def _best_parallel_assignment_allow_empty(
    tr: Trace,
    kids: List[AnyExpr],
    nm_map: Dict[int, Set[str]],
) -> Optional[List[List[str]]]:
    k = len(kids)
    kid_nm = [nm_map[id(ch)] for ch in kids]

    # token must belong to at least one branch
    for m in tr:
        if all(m not in kid_nm[j] for j in range(k)):
            return None

    best: Optional[List[List[str]]] = None
    best_nonempty = -1

    segs: List[List[str]] = [[] for _ in range(k)]

    def backtrack(i: int) -> None:
        nonlocal best, best_nonempty
        if i == len(tr):
            nonempty = sum(1 for s in segs if len(s) > 0)
            if nonempty > best_nonempty:
                best_nonempty = nonempty
                best = [s.copy() for s in segs]
            return
        m = tr[i]
        for j in range(k):
            if m in kid_nm[j]:
                segs[j].append(m)
                backtrack(i + 1)
                segs[j].pop()

    backtrack(0)
    return best


# =============================================================================
# presplit
#
# 关键点：
# - '.' 顺序结构：先尝试严格成功（完全匹配所有子结构且耗尽 tr）
#   若严格失败：按“从左到右尽量匹配前缀”的方式定位第一个失败子结构并返回它
#   （不会再把整个顺序节点作为 fail_node，也不会盲目输出第一个子结构）
# - '.' 在“定位失败”时对子结构允许是复合结构：用 presplit(child, seg) 来判断 seg 是否能匹配 child
# =============================================================================

def presplit(node: AnyExpr, tr: Trace, nm_map: Dict[int, Set[str]]) -> PreSplit:
    # ε
    if isinstance(node, Empty):
        if len(tr) == 0:
            return PreSplit(True, [(node, tr)], [], "ε ok")
        return PreSplit(False, [], [node], "ε fail (non-empty trace)")

    # Leaf：仍按 TE ⊆ NM 判定（保持你论文表6-4规则1的风格）
    if isinstance(node, Leaf):
        te = te_of_trace(tr)
        nm = nm_map[id(node)]
        if te.issubset(nm):
            return PreSplit(True, [(node, tr)], [], "leaf ok")
        return PreSplit(False, [], [node], f"leaf fail TE={te} NM={nm}")

    # +(…,ε) 的包装
    if isinstance(node, ChoiceWithEps):
        te = te_of_trace(tr)
        for ch in node.children:
            if te.issubset(nm_map[id(ch)]):
                return PreSplit(True, [(ch, tr)], [], f"choice(ε) pick {expr_to_str(ch)}")
        return PreSplit(False, [], [node], "choice(ε) fail")

    # OpNode
    assert isinstance(node, OpNode)
    op = node.op
    kids: List[AnyExpr] = list(node.children)

    # -------------------------
    # 顺序 '.'
    # -------------------------
    if op == ".":
        if len(kids) == 0:
            return PreSplit(True, [(node, tr)], [], "empty seq ok" if len(tr) == 0 else "empty seq fail")

        # (A) 先尝试“严格成功匹配”：必须切成 k 段（每段非空），并且最后耗尽 tr
        def strict_match_from(i: int, rest: Trace) -> Optional[List[Tuple[AnyExpr, Trace]]]:
            if i == len(kids):
                return [] if len(rest) == 0 else None

            ch = kids[i]
            remaining_children = len(kids) - i

            # 严格匹配：每个 child 至少要 1 个 token
            if len(rest) < remaining_children:
                return None

            max_end = len(rest) - (remaining_children - 1)
            for end in range(1, max_end + 1):
                seg = rest[:end]
                # 先用 NM 快速剪枝
                if not te_of_trace(seg).issubset(nm_map[id(ch)]):
                    continue
                # 再用 presplit(child, seg) 判断 seg 是否能匹配 child（允许 child 是复合结构）
                if not presplit(ch, seg, nm_map).success:
                    continue
                sub = strict_match_from(i + 1, rest[end:])
                if sub is not None:
                    return [(ch, seg)] + sub
            return None

        strict_pairs = strict_match_from(0, tr)
        if strict_pairs is not None:
            return PreSplit(True, strict_pairs, [], "seq ok (strict)")

        # (B) 严格失败：定位“第一个失败子结构”
        #     规则：从左到右尽量匹配前缀，一旦某个 child 找不到任何非空 seg 可以匹配，就返回该 child
        rest = tr
        pairs_prefix: List[Tuple[AnyExpr, Trace]] = []
        for i, ch in enumerate(kids):
            if len(rest) == 0:
                return PreSplit(False, pairs_prefix, [ch], f"seq fail at child[{i}]={expr_to_str(ch)}: no tokens left")

            found = False
            # 这里不再要求“给后续每个 child 留 1 token”，因为我们只是诊断定位最早失败点
            for end in range(1, len(rest) + 1):
                seg = rest[:end]
                if not te_of_trace(seg).issubset(nm_map[id(ch)]):
                    continue
                if presplit(ch, seg, nm_map).success:
                    pairs_prefix.append((ch, seg))
                    rest = rest[end:]
                    found = True
                    break

            if not found:
                return PreSplit(False, pairs_prefix, [ch], f"seq fail at child[{i}]={expr_to_str(ch)}: cannot match any prefix segment")

        # 理论上不会到这里
        return PreSplit(False, pairs_prefix, [kids[-1]], "seq fail (unexpected)")

    # -------------------------
    # 选择 '+'
    # -------------------------
    if op == "+":
        te = te_of_trace(tr)
        for ch in kids:
            if te.issubset(nm_map[id(ch)]):
                return PreSplit(True, [(ch, tr)], [], f"choice pick {expr_to_str(ch)}")
        return PreSplit(False, [], [node], "choice fail")

    # -------------------------
    # 并行 '|'
    # -------------------------
    if op == "|":
        k = len(kids)
        if k == 0:
            return PreSplit(True, [(node, tr)], [], "empty par ok" if len(tr) == 0 else "empty par fail")

        kid_nm = [nm_map[id(ch)] for ch in kids]
        segs: List[List[str]] = [[] for _ in range(k)]

        # 严格：每分支非空
        def backtrack_strict(i: int) -> bool:
            if i == len(tr):
                return all(len(s) > 0 for s in segs)
            m = tr[i]
            for j in range(k):
                if m in kid_nm[j]:
                    segs[j].append(m)
                    if backtrack_strict(i + 1):
                        return True
                    segs[j].pop()
            return False

        if backtrack_strict(0):
            pairs = [(kids[j], tuple(segs[j])) for j in range(k)]
            return PreSplit(True, pairs, [], "par strict ok")

        # 严格失败：诊断缺失分支 -> fail_nodes=空分支子树
        best = _best_parallel_assignment_allow_empty(tr, kids, nm_map)
        if best is None:
            return PreSplit(False, [], [node], "par fail: token not in any branch")

        empty_children = [kids[j] for j in range(k) if len(best[j]) == 0]
        if empty_children:
            return PreSplit(
                False,
                [],
                empty_children,
                "par strict fail; missing branches=" + ",".join(expr_to_str(x) for x in empty_children),
            )

        return PreSplit(False, [], [node], "par strict fail (unexpected)")

    return PreSplit(False, [], [node], f"unknown op '{op}'")


# =============================================================================
# compute_min_as
#
# 关键修复点：
# - presplit 在顺序层失败时会返回“第一个失败子结构”，不会再返回整个顺序节点
# - 若 fail_nodes 中有复合结构，继续递归细化
# =============================================================================

def compute_min_as(
    expr_root: Optional[Expr] = None,
    abnormal_tr_raw: Trace = (),
    msg_set: Optional[Set[str]] = None,
    debug: bool = True,
    simplify_debug: bool = False,
    **kwargs,
) -> Set[AnyExpr]:
    # 兼容老调用：compute_min_as(expr=...)
    if expr_root is None and "expr" in kwargs:
        expr_root = kwargs["expr"]
    if expr_root is None:
        raise TypeError("compute_min_as() missing required argument: expr_root (or expr=...)")
    if msg_set is None:
        msg_set = set()

    abnormal_tr = filter_trace_to_messages(abnormal_tr_raw, msg_set)
    simplified_root = simplify_tree_to_messages(expr_root, msg_set, debug=simplify_debug)
    nm_map = build_nm_map(simplified_root)

    if debug:
        print("[FILTER] raw_tr =", abnormal_tr_raw, "-> msg_only_tr =", abnormal_tr)
        print("[TREE]   simplified =", expr_to_str(simplified_root))

    minas: Set[AnyExpr] = set()
    visited: Set[Tuple[int, Trace]] = set()  # 防止死递归：同一节点+同一trace 不重复处理

    def refine_fail_node(fn: AnyExpr, tr_for_fn: Trace, depth: int) -> None:
        """对失败节点进一步细化：如果是复合结构就继续递归，直到落到更小节点（最好是 Leaf）。"""
        indent = "  " * depth
        if isinstance(fn, (Leaf, Empty, ChoiceWithEps)):
            minas.add(fn)
            if debug:
                print(f"{indent}  [MinAS ADD] {expr_to_str(fn)}")
            return

        # 复合结构：继续下钻
        if debug:
            print(f"{indent}  [REFINE] fail_node={expr_to_str(fn)} is composite, refine deeper...")
        cal_min_as(fn, tr_for_fn, depth + 1)

    def cal_min_as(node: AnyExpr, tr: Trace, depth: int = 0) -> bool:
        key = (id(node), tr)
        if key in visited:
            return True
        visited.add(key)

        indent = "  " * depth
        ps = presplit(node, tr, nm_map)

        if debug:
            print(f"{indent}[PreSplit] node={expr_to_str(node)} tr={tr} -> success={ps.success} reason={ps.reason}")

        if not ps.success:
            # ⭐关键：顺序结构失败时 ps.pairs 里可能含有“已匹配前缀”，fail_nodes 是第一个失败子结构
            # 我们需要估计该失败子结构对应的 trace 片段：
            # - 对 '.'：失败子结构一般应匹配“剩余 rest”
            # - 对其他结构：直接用当前 tr 细化也能工作（尤其并行缺分支）
            if isinstance(node, OpNode) and node.op == "." and ps.pairs:
                # 计算 rest = tr 去掉已匹配前缀
                consumed = sum(len(seg) for _, seg in ps.pairs)
                rest = tr[consumed:]
                for fn in ps.fail_nodes:
                    refine_fail_node(fn, rest, depth)
            else:
                for fn in ps.fail_nodes:
                    refine_fail_node(fn, tr, depth)

            return False

        all_ok = True
        for ch, sub_tr in ps.pairs:
            ok = cal_min_as(ch, sub_tr, depth + 1)
            all_ok = all_ok and ok
        return all_ok

    cal_min_as(simplified_root, abnormal_tr, 0)
    return minas


def minas_to_strings(minas: Set[AnyExpr]) -> List[str]:
    return sorted({expr_to_str(e) for e in minas})


def simplify_tree_str(expr_root: Expr, msg_set: Set[str], debug: bool = False) -> str:
    simplified_root = simplify_tree_to_messages(expr_root, msg_set, debug=debug)
    return expr_to_str(simplified_root)