from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Literal

from .model import Node, Trace

Mode = Literal["closed", "open"]
CLOSED: Mode = "closed"
OPEN: Mode = "open"


@dataclass(frozen=True, slots=True)
class PatternEnumeration:
    closed: frozenset[Trace]
    open: frozenset[Trace]
    truncated: bool = False

    @property
    def all(self) -> frozenset[Trace]:
        return self.closed | self.open


def _bounded(values: Iterable[Trace], limit: int) -> tuple[frozenset[Trace], bool]:
    result: set[Trace] = set()
    for value in values:
        result.add(value)
        if len(result) > limit:
            result.remove(value)
            return frozenset(result), True
    return frozenset(result), False


def _concat(left: Iterable[Trace], right: Iterable[Trace], limit: int) -> tuple[frozenset[Trace], bool]:
    ordered_left = sorted(left, key=lambda trace: (len(trace), trace))
    ordered_right = sorted(right, key=lambda trace: (len(trace), trace))
    return _bounded((a + b for a in ordered_left for b in ordered_right), limit)


def _interleavings(left: Trace, right: Trace):
    if not left:
        yield right
        return
    if not right:
        yield left
        return
    for tail in _interleavings(left[1:], right):
        yield (left[0],) + tail
    for tail in _interleavings(left, right[1:]):
        yield (right[0],) + tail


def _shuffle(left: Iterable[Trace], right: Iterable[Trace], limit: int) -> tuple[frozenset[Trace], bool]:
    ordered_left = sorted(left, key=lambda trace: (len(trace), trace))
    ordered_right = sorted(right, key=lambda trace: (len(trace), trace))
    return _bounded(
        (trace for a in ordered_left for b in ordered_right for trace in _interleavings(a, b)),
        limit,
    )


def _union(*groups: Iterable[Trace], limit: int) -> tuple[frozenset[Trace], bool]:
    return _bounded(
        (
            value
            for group in groups
            for value in sorted(group, key=lambda trace: (len(trace), trace))
        ),
        limit,
    )


def enumerate_patterns(
    node: Node,
    sends: frozenset[str],
    receives: frozenset[str],
    *,
    limit: int = 10_000,
) -> PatternEnumeration:







    if limit < 1:
        raise ValueError("pattern limit must be positive")
    if node.operator == "tau":
        return PatternEnumeration(frozenset(), frozenset(((),)))
    if node.operator == "leaf":
        name = node.name or ""
        if name in sends:
            return PatternEnumeration(frozenset(), frozenset(((name,),)))
        if name in receives:
            return PatternEnumeration(frozenset(((),)), frozenset(((name,),)))
        raise ValueError(f"message {name!r} has no send/receive direction")

    children = [enumerate_patterns(child, sends, receives, limit=limit) for child in node.children]
    result = children[0]
    for other in children[1:]:
        truncated = result.truncated or other.truncated
        if node.operator == "seq":
            opened_closed, cut1 = _concat(result.open, other.closed, limit)
            closed, cut2 = _union(result.closed, opened_closed, limit=limit)
            opened, cut3 = _concat(result.open, other.open, limit)
            result = PatternEnumeration(closed, opened, truncated or cut1 or cut2 or cut3)
        elif node.operator == "choice":
            closed, cut1 = _union(result.closed, other.closed, limit=limit)
            opened, cut2 = _union(result.open, other.open, limit=limit)
            result = PatternEnumeration(closed, opened, truncated or cut1 or cut2)
        elif node.operator == "parallel":
            cc, cut1 = _shuffle(result.closed, other.closed, limit)
            co, cut2 = _shuffle(result.closed, other.open, limit)
            oc, cut3 = _shuffle(result.open, other.closed, limit)
            closed, cut4 = _union(cc, co, oc, limit=limit)
            opened, cut5 = _shuffle(result.open, other.open, limit)
            result = PatternEnumeration(
                closed,
                opened,
                truncated or cut1 or cut2 or cut3 or cut4 or cut5,
            )
        else:  
            raise ValueError(f"unsupported operator: {node.operator}")
    return result


def accepted_modes(
    node: Node,
    trace: Trace,
    sends: frozenset[str],
    receives: frozenset[str],
) -> frozenset[Mode]:


    @lru_cache(maxsize=None)
    def solve(current: Node, current_trace: Trace) -> frozenset[Mode]:
        if current.operator == "tau":
            return frozenset((OPEN,)) if not current_trace else frozenset()
        if current.operator == "leaf":
            name = current.name or ""
            modes: set[Mode] = set()
            if name in sends:
                if current_trace == (name,):
                    modes.add(OPEN)
            elif name in receives:
                if not current_trace:
                    modes.add(CLOSED)
                if current_trace == (name,):
                    modes.add(OPEN)
            else:
                raise ValueError(f"message {name!r} has no send/receive direction")
            return frozenset(modes)

        if current.operator == "choice":
            return frozenset().union(*(solve(child, current_trace) for child in current.children))

        if current.operator == "parallel":
            child_messages = [child.messages for child in current.children]
            projected: list[list[str]] = [[] for _ in current.children]
            for token in current_trace:
                owners = [index for index, names in enumerate(child_messages) if token in names]
                if len(owners) != 1:
                    return frozenset()
                projected[owners[0]].append(token)
            child_modes = [
                solve(child, tuple(part))
                for child, part in zip(current.children, projected)
            ]
            if any(not modes for modes in child_modes):
                return frozenset()
            modes: set[Mode] = set()
            if all(OPEN in item for item in child_modes):
                modes.add(OPEN)
            if any(CLOSED in item for item in child_modes):
                modes.add(CLOSED)
            return frozenset(modes)

        if current.operator == "seq":
            children = current.children

            @lru_cache(maxsize=None)
            def open_suffix(child_index: int, position: int) -> bool:
                if child_index == len(children):
                    return position == len(current_trace)
                for end in range(position, len(current_trace) + 1):
                    if OPEN in solve(children[child_index], current_trace[position:end]):
                        if open_suffix(child_index + 1, end):
                            return True
                return False

            @lru_cache(maxsize=None)
            def closed_suffix(child_index: int, position: int) -> bool:
                if child_index == len(children):
                    return False
                child = children[child_index]
                if CLOSED in solve(child, current_trace[position:]):
                    return True
                for end in range(position, len(current_trace) + 1):
                    if OPEN in solve(child, current_trace[position:end]):
                        if closed_suffix(child_index + 1, end):
                            return True
                return False

            modes: set[Mode] = set()
            if open_suffix(0, 0):
                modes.add(OPEN)
            if closed_suffix(0, 0):
                modes.add(CLOSED)
            return frozenset(modes)

        raise ValueError(f"unsupported operator: {current.operator}")

    return solve(node, trace)


def accepts_trace(
    node: Node,
    trace: Trace,
    sends: frozenset[str],
    receives: frozenset[str],
) -> bool:
    return bool(accepted_modes(node, trace, sends, receives))
