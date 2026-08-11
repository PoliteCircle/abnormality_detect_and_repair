from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .model import (
    Node,
    PathKey,
    Trace,
    node_at_path,
    node_to_expression,
    project_trace,
)


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    child_path: PathKey
    child_expression: str
    trace: Trace


@dataclass(frozen=True, slots=True)
class SplitStep:
    path: PathKey
    expression: str
    trace: Trace
    success: bool
    reason: str
    assignments: tuple[SplitAssignment, ...] = ()


@dataclass(frozen=True, slots=True)
class MinimalAbnormalStructure:
    path: PathKey
    node: Node
    assigned_trace: Trace
    source_index: int
    line_number: int
    global_trace: Trace
    process_trace: Trace
    reason: str
    child_slice: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    minimal_structures: tuple[MinimalAbnormalStructure, ...]
    steps: tuple[SplitStep, ...]


@dataclass(frozen=True, slots=True)
class RepairScope:
    path: PathKey
    node: Node
    kind: Literal["minimal", "merged"]
    observations: tuple[Trace, ...]
    source_paths: tuple[PathKey, ...]
    source_indices: tuple[int, ...]
    child_slice: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class _PreSplit:
    success: bool
    reason: str
    assignments: tuple[tuple[int, Trace], ...] = ()
    failure_slice: tuple[int, int] | None = None


def _pre_split(node: Node, trace: Trace) -> _PreSplit:
    """Apply the structural pre-splitting rules from Chapter 7 Table 7-4."""

    if node.operator == "tau":
        return _PreSplit(not trace, "tau matches empty trace" if not trace else "tau cannot consume messages")
    if node.operator == "leaf":
        expected = (node.name or "",)
        if trace == expected:
            return _PreSplit(True, "message leaf matched once")
        if not trace:
            return _PreSplit(False, f"message {node.name} is missing")
        return _PreSplit(False, f"expected {expected}, received {trace}")

    if not trace:
        return _PreSplit(False, f"empty trace cannot expose any branch of {node.operator}")

    child_messages = [child.messages for child in node.children]
    owners: list[int] = []
    for token in trace:
        candidates = [index for index, names in enumerate(child_messages) if token in names]
        if len(candidates) != 1:
            if not candidates:
                return _PreSplit(False, f"message {token!r} is outside NM of every child")
            return _PreSplit(False, f"message {token!r} belongs to multiple child subtrees")
        owners.append(candidates[0])

    if node.operator == "seq":
        if owners != sorted(owners):
            inversions = [
                (right, left)
                for left, right in zip(owners, owners[1:])
                if left > right
            ]
            low = min(pair[0] for pair in inversions)
            high = max(pair[1] for pair in inversions)
            return _PreSplit(
                False,
                "child order in the trace contradicts the sequential process-tree order",
                failure_slice=(low, high),
            )
        parts: list[list[str]] = [[] for _ in node.children]
        for token, owner in zip(trace, owners):
            parts[owner].append(token)
        return _PreSplit(
            True,
            "trace was split into ordered child projections",
            tuple((index, tuple(part)) for index, part in enumerate(parts)),
        )

    if node.operator == "choice":
        branches = set(owners)
        if len(branches) != 1:
            return _PreSplit(
                False,
                "one trace exposes multiple mutually-exclusive branches",
            )
        branch = owners[0]
        return _PreSplit(True, f"trace selected choice branch {branch + 1}", ((branch, trace),))

    if node.operator == "parallel":
        parts = [[] for _ in node.children]
        for token, owner in zip(trace, owners):
            parts[owner].append(token)
        return _PreSplit(
            True,
            "trace was projected onto every parallel branch; empty projections are retained",
            tuple((index, tuple(part)) for index, part in enumerate(parts)),
        )

    return _PreSplit(False, f"unsupported operator {node.operator}")


def find_minimal_abnormal_structures(
    root: Node,
    process_trace: Trace,
    *,
    source_index: int,
    line_number: int,
    global_trace: Trace,
) -> DiagnosisResult:
    """Run Algorithm 6 and retain every intermediate pre-splitting decision."""

    records: list[MinimalAbnormalStructure] = []
    steps: list[SplitStep] = []

    def visit(node: Node, trace: Trace, path: PathKey) -> bool:
        split = _pre_split(node, trace)
        assignments = tuple(
            SplitAssignment(
                path + (index,),
                node_to_expression(node.children[index]),
                assigned,
            )
            for index, assigned in split.assignments
        )
        steps.append(
            SplitStep(
                path,
                node_to_expression(node),
                trace,
                split.success,
                split.reason,
                assignments,
            )
        )
        if not split.success:
            abnormal_node = node
            assigned_trace = trace
            if split.failure_slice is not None:
                start, end = split.failure_slice
                abnormal_node = Node.composite("seq", node.children[start : end + 1])
                assigned_trace = tuple(
                    token for token in trace if token in abnormal_node.messages
                )
            records.append(
                MinimalAbnormalStructure(
                    path=path,
                    node=abnormal_node,
                    assigned_trace=assigned_trace,
                    source_index=source_index,
                    line_number=line_number,
                    global_trace=global_trace,
                    process_trace=process_trace,
                    reason=split.reason,
                    child_slice=split.failure_slice,
                )
            )
            return False

        success = True
        for index, assigned in split.assignments:
            if not visit(node.children[index], assigned, path + (index,)):
                success = False
        return success

    visit(root, process_trace, ())
    return DiagnosisResult(tuple(records), tuple(steps))


def build_repair_scopes(
    root: Node,
    records: Iterable[MinimalAbnormalStructure],
) -> tuple[RepairScope, ...]:
    """Group equal MinAS nodes and merge siblings at their minimal ancestor.

    The merged scope implements Definition 7.16.  For every scope, the trace
    used for repair is the original abnormal process trace projected onto the
    scope's message set.
    """

    items = list(records)
    if not items:
        return ()

    groups: dict[
        tuple[PathKey, tuple[int, int] | None],
        tuple[str, list[MinimalAbnormalStructure]],
    ] = {}
    by_path: dict[
        tuple[PathKey, tuple[int, int] | None],
        list[MinimalAbnormalStructure],
    ] = {}
    for item in items:
        by_path.setdefault((item.path, item.child_slice), []).append(item)
    for key, grouped in by_path.items():
        groups[key] = ("minimal", grouped)

    prefixes: set[PathKey] = set()
    tree_items = [item for item in items if item.child_slice is None]
    for item in tree_items:
        prefixes.update(item.path[:depth] for depth in range(len(item.path)))
    for prefix in prefixes:
        descendants = [item for item in tree_items if item.path[: len(prefix)] == prefix]
        immediate_branches = {
            item.path[len(prefix)]
            for item in descendants
            if len(item.path) > len(prefix)
        }
        key = (prefix, None)
        if len(immediate_branches) >= 2 and key not in groups:
            groups[key] = ("merged", descendants)

    scopes: list[RepairScope] = []
    for (path, child_slice), (kind, grouped) in groups.items():
        node = grouped[0].node if child_slice is not None else node_at_path(root, path)
        observations = sorted(
            {
                project_trace(item.process_trace, node.messages)
                for item in grouped
            },
            key=lambda trace: (len(trace), trace),
        )
        scopes.append(
            RepairScope(
                path=path,
                node=node,
                kind=kind,  # type: ignore[arg-type]
                observations=tuple(observations),
                source_paths=tuple(sorted({item.path for item in grouped})),
                source_indices=tuple(sorted({item.source_index for item in grouped})),
                child_slice=child_slice,
            )
        )

    scopes.sort(key=lambda scope: (-len(scope.path), scope.path, scope.kind))
    return tuple(scopes)
