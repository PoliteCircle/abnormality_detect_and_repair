from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Union, Optional

from ast_expr import Expr, Leaf, OpNode

Trace = Tuple[str, ...]







@dataclass(frozen=True)
class Empty:

    pass


EPS = Empty()
AnyExpr = Union[Expr, Empty]


def is_empty(e: AnyExpr) -> bool:
    return isinstance(e, Empty)






def expr_to_str(e: AnyExpr) -> str:
    if isinstance(e, Empty):
        return "ε"
    if isinstance(e, Leaf):
        return e.name

    return f"{e.op}(" + ",".join(expr_to_str(c) for c in e.children) + ")"






def filter_trace_to_messages(tr: Trace, msg_set: Set[str]) -> Trace:
    return tuple(x for x in tr if x in msg_set)










@dataclass(frozen=True)
class ChoiceWithEps:

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






@dataclass
class PreSplit:
    success: bool
    pairs: List[Tuple[AnyExpr, Trace]]
    fail_nodes: List[AnyExpr]
    reason: str = ""






def _best_parallel_assignment_allow_empty(
    tr: Trace,
    kids: List[AnyExpr],
    nm_map: Dict[int, Set[str]],
) -> Optional[List[List[str]]]:
    k = len(kids)
    kid_nm = [nm_map[id(ch)] for ch in kids]


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












def presplit(node: AnyExpr, tr: Trace, nm_map: Dict[int, Set[str]]) -> PreSplit:

    if isinstance(node, Empty):
        if len(tr) == 0:
            return PreSplit(True, [(node, tr)], [], "ε ok")
        return PreSplit(False, [], [node], "ε fail (non-empty trace)")


    if isinstance(node, Leaf):
        te = te_of_trace(tr)
        nm = nm_map[id(node)]
        if te.issubset(nm):
            return PreSplit(True, [(node, tr)], [], "leaf ok")
        return PreSplit(False, [], [node], f"leaf fail TE={te} NM={nm}")


    if isinstance(node, ChoiceWithEps):
        te = te_of_trace(tr)
        for ch in node.children:
            if te.issubset(nm_map[id(ch)]):
                return PreSplit(True, [(ch, tr)], [], f"choice(ε) pick {expr_to_str(ch)}")
        return PreSplit(False, [], [node], "choice(ε) fail")


    assert isinstance(node, OpNode)
    op = node.op
    kids: List[AnyExpr] = list(node.children)




    if op == ".":
        if len(kids) == 0:
            return PreSplit(True, [(node, tr)], [], "empty seq ok" if len(tr) == 0 else "empty seq fail")


        def strict_match_from(i: int, rest: Trace) -> Optional[List[Tuple[AnyExpr, Trace]]]:
            if i == len(kids):
                return [] if len(rest) == 0 else None

            ch = kids[i]
            remaining_children = len(kids) - i


            if len(rest) < remaining_children:
                return None

            max_end = len(rest) - (remaining_children - 1)
            for end in range(1, max_end + 1):
                seg = rest[:end]

                if not te_of_trace(seg).issubset(nm_map[id(ch)]):
                    continue

                if not presplit(ch, seg, nm_map).success:
                    continue
                sub = strict_match_from(i + 1, rest[end:])
                if sub is not None:
                    return [(ch, seg)] + sub
            return None

        strict_pairs = strict_match_from(0, tr)
        if strict_pairs is not None:
            return PreSplit(True, strict_pairs, [], "seq ok (strict)")



        rest = tr
        pairs_prefix: List[Tuple[AnyExpr, Trace]] = []
        for i, ch in enumerate(kids):
            if len(rest) == 0:
                return PreSplit(False, pairs_prefix, [ch], f"seq fail at child[{i}]={expr_to_str(ch)}: no tokens left")

            found = False

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


        return PreSplit(False, pairs_prefix, [kids[-1]], "seq fail (unexpected)")




    if op == "+":
        te = te_of_trace(tr)
        for ch in kids:
            if te.issubset(nm_map[id(ch)]):
                return PreSplit(True, [(ch, tr)], [], f"choice pick {expr_to_str(ch)}")
        return PreSplit(False, [], [node], "choice fail")




    if op == "|":
        k = len(kids)
        if k == 0:
            return PreSplit(True, [(node, tr)], [], "empty par ok" if len(tr) == 0 else "empty par fail")

        kid_nm = [nm_map[id(ch)] for ch in kids]
        segs: List[List[str]] = [[] for _ in range(k)]


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










def compute_min_as(
    expr_root: Optional[Expr] = None,
    abnormal_tr_raw: Trace = (),
    msg_set: Optional[Set[str]] = None,
    debug: bool = True,
    simplify_debug: bool = False,
    **kwargs,
) -> Set[AnyExpr]:

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
    visited: Set[Tuple[int, Trace]] = set()

    def refine_fail_node(fn: AnyExpr, tr_for_fn: Trace, depth: int) -> None:

        indent = "  " * depth
        if isinstance(fn, (Leaf, Empty, ChoiceWithEps)):
            minas.add(fn)
            if debug:
                print(f"{indent}  [MinAS ADD] {expr_to_str(fn)}")
            return


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




            if isinstance(node, OpNode) and node.op == "." and ps.pairs:

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
