from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Literal, Sequence

Operator = Literal["leaf", "tau", "seq", "choice", "parallel"]
PathKey = tuple[int, ...]
Trace = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Node:






    operator: Operator
    name: str | None = None
    children: tuple["Node", ...] = ()

    def __post_init__(self) -> None:
        if self.operator == "leaf":
            if not self.name or self.children:
                raise ValueError("leaf nodes require a name and no children")
            return
        if self.operator == "tau":
            if self.name is not None or self.children:
                raise ValueError("tau nodes cannot have a name or children")
            return
        if self.name is not None:
            raise ValueError(f"{self.operator} nodes cannot have a name")
        if len(self.children) < 2:
            raise ValueError(f"{self.operator} nodes require at least two children")

    @staticmethod
    def leaf(name: str) -> "Node":
        return Node("leaf", name=name)

    @staticmethod
    def tau() -> "Node":
        return Node("tau")

    @staticmethod
    def composite(operator: Literal["seq", "choice", "parallel"], children: Iterable["Node"]) -> "Node":
        prepared = tuple(children)
        if operator in {"seq", "parallel"}:
            prepared = tuple(child for child in prepared if child.operator != "tau")
        deduplicated: list[Node] = []
        for child in prepared:
            if child not in deduplicated:
                deduplicated.append(child)
        prepared = tuple(deduplicated)
        if not prepared:
            return Node.tau()
        if len(prepared) == 1:
            return prepared[0]
        return Node(operator, children=prepared)

    @property
    def messages(self) -> frozenset[str]:
        if self.operator == "leaf":
            return frozenset((self.name,))  
        if self.operator == "tau":
            return frozenset()
        return frozenset().union(*(child.messages for child in self.children))


@dataclass(frozen=True, slots=True)
class ProcessModel:
    process_id: str
    participant_name: str
    tree: Node
    sends: frozenset[str]
    receives: frozenset[str]
    source_bpmn: Path
    process_bpmn: Path
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        overlap = self.sends & self.receives
        if overlap:
            raise ValueError(
                f"process {self.process_id!r} sends and receives the same message names: "
                f"{sorted(overlap)}"
            )
        tree_messages = self.tree.messages
        unknown = tree_messages - (self.sends | self.receives)
        if unknown:
            raise ValueError(
                f"process tree contains messages without send/receive direction: {sorted(unknown)}"
            )


def simplify(node: Node, *, recursive: bool = True) -> Node:


    if node.operator in {"leaf", "tau"}:
        return node

    children = tuple(simplify(child) for child in node.children) if recursive else node.children
    if node.operator in {"seq", "parallel"}:
        children = tuple(child for child in children if child.operator != "tau")

    deduplicated: list[Node] = []
    for child in children:
        if child not in deduplicated:
            deduplicated.append(child)
    children = tuple(deduplicated)

    if not children:
        return Node.tau()
    if len(children) == 1:
        return children[0]
    return Node(node.operator, children=children)


def node_to_expression(node: Node) -> str:
    if node.operator == "leaf":
        return node.name or ""
    if node.operator == "tau":
        return "tau"
    symbol = {"seq": ".", "choice": "+", "parallel": "|"}[node.operator]
    return f"{symbol}(" + ",".join(node_to_expression(child) for child in node.children) + ")"


def path_to_string(path: PathKey) -> str:
    return "root" if not path else "root/" + "/".join(str(index + 1) for index in path)


def walk(node: Node, path: PathKey = ()) -> Iterator[tuple[PathKey, Node]]:
    yield path, node
    for index, child in enumerate(node.children):
        yield from walk(child, path + (index,))


def node_at_path(root: Node, path: PathKey) -> Node:
    current = root
    for index in path:
        current = current.children[index]
    return current


def replace_at_path(root: Node, path: PathKey, replacement: Node) -> Node:
    if not path:
        return simplify(replacement)
    index = path[0]
    children = list(root.children)
    children[index] = replace_at_path(children[index], path[1:], replacement)
    return simplify(Node(root.operator, children=tuple(children)))


def replace_child_slice(
    root: Node,
    container_path: PathKey,
    child_slice: tuple[int, int],
    replacement: Node,
) -> Node:







    container = node_at_path(root, container_path)
    if container.operator not in {"seq", "choice", "parallel"}:
        raise ValueError("child slices can only be replaced inside composite nodes")
    start, end = child_slice
    if not (0 <= start <= end < len(container.children)):
        raise IndexError(f"invalid child slice {child_slice} for {len(container.children)} children")
    children = container.children[:start] + (replacement,) + container.children[end + 1 :]
    rebuilt = Node.composite(container.operator, children)
    return replace_at_path(root, container_path, rebuilt)


def lowest_common_ancestor(paths: Sequence[PathKey]) -> PathKey:
    if not paths:
        raise ValueError("at least one path is required")
    prefix: list[int] = []
    for items in zip(*paths):
        if len(set(items)) != 1:
            break
        prefix.append(items[0])
    return tuple(prefix)


def project_trace(trace: Trace, messages: Iterable[str]) -> Trace:
    visible = frozenset(messages)
    return tuple(token for token in trace if token in visible)


def validate_unique_messages(root: Node) -> None:
    occurrences: dict[str, int] = {}
    for _, node in walk(root):
        if node.operator == "leaf" and node.name:
            occurrences[node.name] = occurrences.get(node.name, 0) + 1
    duplicates = sorted(name for name, count in occurrences.items() if count > 1)
    if duplicates:
        raise ValueError(
            "each message activity must occur once; duplicate tree leaves: "
            + ", ".join(duplicates)
        )


def tree_lines(root: Node) -> list[str]:
    lines: list[str] = []
    for path, node in walk(root):
        depth = len(path)
        label = node.name if node.operator == "leaf" else node.operator
        lines.append(
            f"{'  ' * depth}- {path_to_string(path)}: {label}; "
            f"NM={sorted(node.messages)}"
        )
    return lines
