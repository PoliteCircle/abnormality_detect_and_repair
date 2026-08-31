from __future__ import annotations
from typing import Set, Tuple, Optional
from functools import lru_cache

from ast_expr import Expr, Leaf, OpNode








Token = str
Seq = Tuple[Token, ...]
SeqSet = Set[Seq]
CO = Tuple[SeqSet, SeqSet]
EPS: Seq = tuple()






def concat_sets(A: SeqSet, B: SeqSet) -> SeqSet:
    out: SeqSet = set()
    for a in A:
        for b in B:
            out.add(a + b)
    return out






@lru_cache(maxsize=None)
def _interleave_two(a: Seq, b: Seq) -> Tuple[Seq, ...]:
    if not a:
        return (b,)
    if not b:
        return (a,)
    res = []
    for tail in _interleave_two(a[1:], b):
        res.append((a[0],) + tail)
    for tail in _interleave_two(a, b[1:]):
        res.append((b[0],) + tail)
    return tuple(res)

def interleave_sets(A: SeqSet, B: SeqSet, limit: Optional[int] = None) -> SeqSet:
    out: SeqSet = set()
    for a in A:
        for b in B:
            for s in _interleave_two(a, b):
                out.add(s)
                if limit is not None and len(out) >= limit:
                    return out
    return out







def leaf_rule(name: str, MEo: Set[str], MEi: Set[str]) -> CO:

    if name in MEo:
        return set(), {(name,)}

    if name in MEi:
        return {EPS}, {(name,)}

    return set(), {EPS}






def rule_seq(coX: CO, coY: CO) -> CO:
    closedX, openX = coX
    closedY, openY = coY
    closed = set(closedX) | concat_sets(openX, closedY)
    open_ = concat_sets(openX, openY)
    return closed, open_

def rule_choice(coX: CO, coY: CO) -> CO:
    closedX, openX = coX
    closedY, openY = coY
    return set(closedX) | set(closedY), set(openX) | set(openY)

def rule_parallel(coX: CO, coY: CO, limit: Optional[int] = None) -> CO:
    closedX, openX = coX
    closedY, openY = coY
    closed = (
        interleave_sets(closedX, closedY, limit=limit)
        | interleave_sets(closedX, openY,  limit=limit)
        | interleave_sets(openX,  closedY, limit=limit)
    )
    open_ = interleave_sets(openX, openY, limit=limit)
    return closed, open_

def apply_rule(op: str, coX: CO, coY: CO, interleave_limit: Optional[int]) -> CO:
    if op == ".":
        return rule_seq(coX, coY)
    if op == "+":
        return rule_choice(coX, coY)
    if op == "|":
        return rule_parallel(coX, coY, limit=interleave_limit)
    raise ValueError(f"Unknown operator: {op}")







def calco(expr: Expr, MEo: Set[str], MEi: Set[str], interleave_limit: Optional[int] = None) -> CO:
    if isinstance(expr, Leaf):
        return leaf_rule(expr.name, MEo, MEi)

    assert isinstance(expr, OpNode)
    kids = list(expr.children)
    if len(kids) == 0:
        return set(), {EPS}
    if len(kids) == 1:
        return calco(kids[0], MEo, MEi, interleave_limit)

    r = apply_rule(
        expr.op,
        calco(kids[0], MEo, MEi, interleave_limit),
        calco(kids[1], MEo, MEi, interleave_limit),
        interleave_limit
    )
    for i in range(2, len(kids)):
        r = apply_rule(expr.op, r, calco(kids[i], MEo, MEi, interleave_limit), interleave_limit)
    return r

def tree_to_mp(expr: Expr, MEo: Set[str], MEi: Set[str], interleave_limit: Optional[int] = 2000) -> SeqSet:
    closed, open_ = calco(expr, MEo, MEi, interleave_limit)
    return set(closed) | set(open_)
