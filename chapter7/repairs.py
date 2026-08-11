from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .diagnosis import RepairScope
from .model import (
    Node,
    ProcessModel,
    Trace,
    node_to_expression,
    project_trace,
    replace_at_path,
    replace_child_slice,
)
from .patterns import accepts_trace, enumerate_patterns


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    rule_id: int
    title: str
    rationale: str
    scope_path: tuple[int, ...]
    before_expression: str
    after_expression: str
    replacement: Node
    repaired_process_expression: str
    edit_cost: int
    model_behavior_preserved: bool | None
    scope_observations_covered: int
    scope_observations_total: int
    normal_after_pass: int
    normal_total: int
    abnormal_after_pass: int
    abnormal_total: int
    definition_717_satisfied: bool | None
    warnings: tuple[str, ...] = ()
    scope_child_slice: tuple[int, int] | None = None

    @property
    def preserves_all_observed_normals(self) -> bool:
        return self.normal_after_pass == self.normal_total

    @property
    def covers_all_scope_observations(self) -> bool:
        return self.scope_observations_covered == self.scope_observations_total


@dataclass(frozen=True, slots=True)
class _RawCandidate:
    rule_id: int
    title: str
    rationale: str
    replacement: Node
    edit_cost: int


def _branch_order(node: Node, trace: Trace) -> tuple[int, ...] | None:
    owners: list[int] = []
    child_messages = [child.messages for child in node.children]
    for token in trace:
        candidates = [index for index, messages in enumerate(child_messages) if token in messages]
        if len(candidates) != 1:
            return None
        owner = candidates[0]
        if not owners or owners[-1] != owner:
            owners.append(owner)
    return tuple(owners)


def _raw_candidates(
    scope: RepairScope,
    supporting_observations: Iterable[Trace] = (),
) -> list[_RawCandidate]:
    node = scope.node
    observations = set(scope.observations)
    evidence = observations | set(supporting_observations)
    empty_only = observations == {()}
    candidates: list[_RawCandidate] = []

    if node.operator == "leaf":
        expected = (node.name or "",)
        if empty_only:
            candidates.append(
                _RawCandidate(
                    1,
                    "删除未出现的消息活动",
                    "表7-5方案1：该消息在异常片段中完全缺失，以 tau 替换该活动。",
                    Node.tau(),
                    1,
                )
            )
        if () in observations and expected in evidence:
            candidates.append(
                _RawCandidate(
                    2,
                    "把消息活动改为可选",
                    "表7-5方案2：该消息有时出现、有时缺失，增加 XOR(tau) 分支。",
                    Node.composite("choice", (node, Node.tau())),
                    2,
                )
            )
        return candidates

    if node.operator == "tau":
        return candidates

    orders = {
        order
        for trace in observations
        if trace and (order := _branch_order(node, trace)) is not None
    }
    evidence_orders = {
        order
        for trace in evidence
        if trace and (order := _branch_order(node, trace)) is not None
    }
    nominal = tuple(range(len(node.children)))
    reverse = tuple(reversed(nominal))
    single_branches = {
        order[0]
        for order in orders
        if len(order) == 1
    }

    if node.operator == "seq":
        if empty_only:
            candidates.append(
                _RawCandidate(3, "删除空顺序块", "表7-5方案3：整个顺序结构未发生。", Node.tau(), 2)
            )
        if len(node.children) >= 2 and reverse in orders and nominal not in orders:
            candidates.append(
                _RawCandidate(
                    4,
                    "反转顺序结构",
                    "表7-5方案4：日志只显示与模型相反的稳定次序。",
                    Node.composite("seq", tuple(reversed(node.children))),
                    2,
                )
            )
        if len(node.children) == 2 and reverse in orders and nominal in evidence_orders:
            candidates.append(
                _RawCandidate(
                    5,
                    "顺序改为并行",
                    "表7-5方案5：两个方向的次序都出现，说明两个分支可能并行。",
                    Node.composite("parallel", node.children),
                    2,
                )
            )
        if len(single_branches) >= 2 and all(len(order) == 1 for order in orders):
            selected = tuple(node.children[index] for index in sorted(single_branches))
            candidates.append(
                _RawCandidate(
                    6,
                    "顺序改为排他选择",
                    "表7-5方案6：每次只观察到一个顺序分支，分支可能互斥。",
                    Node.composite("choice", selected),
                    3,
                )
            )

    elif node.operator == "choice":
        if empty_only:
            candidates.append(
                _RawCandidate(7, "删除空选择块", "表7-5方案7：选择结构整体未发生。", Node.tau(), 2)
            )
        combined = [order for order in orders if len(set(order)) >= 2]
        if combined:
            candidates.append(
                _RawCandidate(
                    8,
                    "排他选择改为并行",
                    "表7-5方案8：同一条消息迹包含多个原本互斥的分支。",
                    Node.composite("parallel", node.children),
                    2,
                )
            )
            unique_orders = sorted(set(combined))
            for order in unique_orders:
                if set(order) != set(range(len(node.children))):
                    continue
                replacement = Node.composite("seq", tuple(node.children[index] for index in order))
                candidates.append(
                    _RawCandidate(
                        9,
                        "排他选择改为顺序",
                        "表7-5方案9：多个分支在日志中以稳定次序共同出现；顺序可能不唯一。",
                        replacement,
                        2,
                    )
                )

    elif node.operator == "parallel":
        if empty_only:
            candidates.append(
                _RawCandidate(10, "删除空并行块", "表7-5方案10：并行结构整体未发生。", Node.tau(), 2)
            )
        nonempty_orders = [order for order in orders if order]
        if nonempty_orders and all(len(set(order)) == 1 for order in nonempty_orders):
            active = {order[0] for order in nonempty_orders}
            if len(active) == 1:
                index = next(iter(active))
                candidates.append(
                    _RawCandidate(
                        11,
                        "裁剪从未出现的并行分支",
                        "表7-5方案11：所有片段都只包含同一个并行分支。",
                        node.children[index],
                        2,
                    )
                )
            elif len(active) >= 2:
                selected = tuple(node.children[index] for index in sorted(active))
                candidates.append(
                    _RawCandidate(
                        12,
                        "并行改为排他选择",
                        "表7-5方案12：不同片段各自只出现一个并行分支。",
                        Node.composite("choice", selected),
                        2,
                    )
                )

    deduplicated: dict[tuple[int, str], _RawCandidate] = {}
    for candidate in candidates:
        key = (candidate.rule_id, node_to_expression(candidate.replacement))
        deduplicated.setdefault(key, candidate)
    return list(deduplicated.values())


def generate_repair_candidates(
    model: ProcessModel,
    scope: RepairScope,
    *,
    normal_traces: Iterable[Trace],
    abnormal_traces: Iterable[Trace],
    behavior_limit: int = 20_000,
) -> tuple[RepairCandidate, ...]:
    """Generate Table 7-5 candidates and verify their effects exactly on logs."""

    normals = tuple(normal_traces)
    abnormals = tuple(abnormal_traces)
    supporting_observations = tuple(
        project_trace(trace, scope.node.messages) for trace in normals
    )
    raw_candidates = _raw_candidates(scope, supporting_observations)
    evaluated: list[RepairCandidate] = []

    before_patterns = enumerate_patterns(
        scope.node,
        model.sends,
        model.receives,
        limit=behavior_limit,
    )
    for raw in raw_candidates:
        repaired_tree = (
            replace_child_slice(model.tree, scope.path, scope.child_slice, raw.replacement)
            if scope.child_slice is not None
            else replace_at_path(model.tree, scope.path, raw.replacement)
        )
        after_patterns = enumerate_patterns(
            raw.replacement,
            model.sends,
            model.receives,
            limit=behavior_limit,
        )
        warnings: list[str] = []
        if before_patterns.truncated or after_patterns.truncated:
            model_preserved: bool | None = None
            warnings.append(
                "子树消息模式超过枚举上限，无法证明 MP_before 是 MP_after 的子集；日志验证仍为精确结果。"
            )
        else:
            model_preserved = before_patterns.all <= after_patterns.all

        covered = sum(
            accepts_trace(raw.replacement, trace, model.sends, model.receives)
            for trace in scope.observations
        )
        normal_pass = sum(
            accepts_trace(repaired_tree, trace, model.sends, model.receives)
            for trace in normals
        )
        abnormal_pass = sum(
            accepts_trace(repaired_tree, trace, model.sends, model.receives)
            for trace in abnormals
        )
        if model_preserved is None:
            definition_satisfied: bool | None = None
        else:
            definition_satisfied = model_preserved and covered == len(scope.observations)

        evaluated.append(
            RepairCandidate(
                rule_id=raw.rule_id,
                title=raw.title,
                rationale=raw.rationale,
                scope_path=scope.path,
                before_expression=node_to_expression(scope.node),
                after_expression=node_to_expression(raw.replacement),
                replacement=raw.replacement,
                repaired_process_expression=node_to_expression(repaired_tree),
                edit_cost=raw.edit_cost,
                model_behavior_preserved=model_preserved,
                scope_observations_covered=covered,
                scope_observations_total=len(scope.observations),
                normal_after_pass=normal_pass,
                normal_total=len(normals),
                abnormal_after_pass=abnormal_pass,
                abnormal_total=len(abnormals),
                definition_717_satisfied=definition_satisfied,
                warnings=tuple(warnings),
                scope_child_slice=scope.child_slice,
            )
        )

    evaluated.sort(
        key=lambda candidate: (
            not candidate.preserves_all_observed_normals,
            -candidate.abnormal_after_pass,
            candidate.definition_717_satisfied is not True,
            candidate.edit_cost,
            candidate.rule_id,
            candidate.after_expression,
        )
    )
    return tuple(evaluated)
