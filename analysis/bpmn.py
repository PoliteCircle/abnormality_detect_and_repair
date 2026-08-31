from __future__ import annotations

import copy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .model import Node, ProcessModel, simplify, validate_unique_messages

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
class UnsupportedModelError(RuntimeError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _qname(local: str) -> str:
    return f"{{{BPMN_NS}}}{local}"


def _normalise_token(value: str) -> str:
    value = "_".join(value.strip().split())
    return re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]", "_", value)


def _new_id(existing: set[str], base: str) -> str:
    candidate = base
    index = 1
    while candidate in existing:
        candidate = f"{base}_{index}"
        index += 1
    existing.add(candidate)
    return candidate


def normalise_collaboration(source: Path, destination: Path) -> Path:


    tree = ET.parse(source)
    root = tree.getroot()
    existing = {element.get("id") for element in root.iter() if element.get("id")}
    event_definitions = {
        "messageEventDefinition",
        "timerEventDefinition",
        "signalEventDefinition",
        "conditionalEventDefinition",
        "linkEventDefinition",
        "errorEventDefinition",
        "escalationEventDefinition",
        "compensateEventDefinition",
        "terminateEventDefinition",
        "cancelEventDefinition",
        "multipleEventDefinition",
        "parallelMultipleEventDefinition",
    }

    for process in root.findall(f".//{{{BPMN_NS}}}process"):
        message_starts: list[ET.Element] = []
        for element in list(process):
            kind = _local(element.tag)
            if kind == "eventBasedGateway":
                element.tag = _qname("exclusiveGateway")
            elif kind == "startEvent":
                if any(_local(child.tag) == "messageEventDefinition" for child in element):
                    message_starts.append(element)
            elif kind == "endEvent":
                continue
            elif kind.endswith("Event"):
                element.tag = _qname("task")
                for child in list(element):
                    if _local(child.tag) in event_definitions:
                        element.remove(child)

        for event in message_starts:
            event_id = event.get("id")
            if not event_id:
                continue
            event.tag = _qname("task")
            for child in list(event):
                if _local(child.tag) in event_definitions:
                    event.remove(child)
            start_id = _new_id(existing, f"{event_id}_none_start")
            flow_id = _new_id(existing, f"{event_id}_none_start_flow")
            start = ET.Element(_qname("startEvent"), {"id": start_id})
            outgoing = ET.SubElement(start, _qname("outgoing"))
            outgoing.text = flow_id
            incoming = ET.Element(_qname("incoming"))
            incoming.text = flow_id
            event.insert(0, incoming)
            flow = ET.Element(
                _qname("sequenceFlow"),
                {"id": flow_id, "sourceRef": start_id, "targetRef": event_id},
            )
            process.extend((start, flow))

    for element in root.iter():
        if element.get("name"):
            element.set("name", _normalise_token(element.get("name") or ""))

    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def _node_to_process(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for process in root.findall(f".//{{{BPMN_NS}}}process"):
        process_id = process.get("id")
        if not process_id:
            continue
        for element in process.iter():
            if element.get("id"):
                result[element.get("id") or ""] = process_id
    return result


def _export_process(root: ET.Element, process_id: str, destination: Path) -> None:
    exported_root = copy.deepcopy(root)
    for child in list(exported_root):
        kind = _local(child.tag)
        if kind == "collaboration" or kind == "BPMNDiagram":
            exported_root.remove(child)
        elif kind == "process" and child.get("id") != process_id:
            exported_root.remove(child)

    target_process = next(
        process
        for process in exported_root.findall(f".//{{{BPMN_NS}}}process")
        if process.get("id") == process_id
    )
    task_kinds = {
        "task",
        "userTask",
        "serviceTask",
        "manualTask",
        "businessRuleTask",
        "scriptTask",
        "sendTask",
        "receiveTask",
        "callActivity",
    }
    for element in target_process.iter():
        if _local(element.tag) in task_kinds and element.get("id"):
            element.set("name", element.get("id") or "")

    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(exported_root).write(destination, encoding="utf-8", xml_declaration=True)


def _convert_pm4py_tree(process_tree, endpoint_to_message: dict[str, str]) -> Node:
    from pm4py.objects.process_tree.obj import Operator

    if process_tree.operator is None:
        label = process_tree.label
        if label is None:
            return Node.tau()
        return Node.leaf(endpoint_to_message[label]) if label in endpoint_to_message else Node.tau()

    children = tuple(_convert_pm4py_tree(child, endpoint_to_message) for child in process_tree.children)
    if process_tree.operator == Operator.SEQUENCE:
        return Node.composite("seq", children)
    if process_tree.operator == Operator.XOR:
        return Node.composite("choice", children)
    if process_tree.operator == Operator.PARALLEL:
        return Node.composite("parallel", children)
    raise UnsupportedModelError(
        "the process must be structured and acyclic, with every activity occurring once; "
        f"PM4Py produced unsupported operator {process_tree.operator!s}."
    )


class _ReducedTreeParser:









    def __init__(self, expression: str, endpoint_to_message: dict[str, str]) -> None:
        self.expression = expression
        self.endpoint_to_message = endpoint_to_message
        self.position = 0

    def parse(self) -> Node:
        stripped = self.expression.strip()
        if stripped and not any(symbol in stripped for symbol in ("(", ")", ",")):
            message = self.endpoint_to_message.get(stripped)
            return Node.leaf(message) if message else Node.tau()
        node = self._node()
        self._whitespace()
        if self.position != len(self.expression):
            raise UnsupportedModelError(
                f"unexpected reduced process-tree suffix at position {self.position}: "
                f"{self.expression[self.position:self.position + 40]!r}"
            )
        return node

    def _whitespace(self) -> None:
        while self.position < len(self.expression) and self.expression[self.position].isspace():
            self.position += 1

    def _consume(self, value: str) -> None:
        self._whitespace()
        if not self.expression.startswith(value, self.position):
            raise UnsupportedModelError(
                f"expected {value!r} at position {self.position} in reduced process tree"
            )
        self.position += len(value)

    def _quoted_label(self) -> str:
        self._consume("'")
        characters: list[str] = []
        while self.position < len(self.expression):
            character = self.expression[self.position]
            self.position += 1
            if character == "'":
                return "".join(characters)
            if character == "\\" and self.position < len(self.expression):
                characters.append(self.expression[self.position])
                self.position += 1
            else:
                characters.append(character)
        raise UnsupportedModelError("unterminated quoted activity in reduced process tree")

    def _node(self) -> Node:
        self._whitespace()
        if self.position >= len(self.expression):
            raise UnsupportedModelError("unexpected end of reduced process tree")
        if self.expression.startswith("tau", self.position):
            self.position += 3
            return Node.tau()
        if self.expression[self.position] in {"τ", "τ"}:
            self.position += 1
            return Node.tau()
        if self.expression[self.position] == "'":
            label = self._quoted_label()
            message = self.endpoint_to_message.get(label)
            return Node.leaf(message) if message else Node.tau()

        operator = None
        for symbol in ("->", "+", "X", "*", "O", "<>"):
            if self.expression.startswith(symbol, self.position):
                operator = symbol
                self.position += len(symbol)
                break
        if operator is None:
            raise UnsupportedModelError(
                f"unsupported reduced process-tree token at position {self.position}: "
                f"{self.expression[self.position:self.position + 20]!r}"
            )
        if operator in {"*", "O", "<>"}:
            raise UnsupportedModelError(
                f"reduced process-tree operator {operator!r} is not supported"
            )

        self._consume("(")
        children: list[Node] = []
        while True:
            children.append(self._node())
            self._whitespace()
            if self.position >= len(self.expression):
                raise UnsupportedModelError("unterminated composite in reduced process tree")
            if self.expression[self.position] == ",":
                self.position += 1
                continue
            if self.expression[self.position] == ")":
                self.position += 1
                break
            raise UnsupportedModelError(
                f"expected ',' or ')' at position {self.position} in reduced process tree"
            )
        mapped = {"->": "seq", "+": "parallel", "X": "choice"}[operator]
        return Node.composite(mapped, children)


def _convert_reduced_wf_net(bpmn_graph, endpoint_to_message: dict[str, str]) -> Node:


    import pm4py
    from pm4py.objects.conversion.wf_net.variants import to_process_tree

    net, initial_marking, final_marking = pm4py.convert_to_petri_net(bpmn_graph)
    grouped = to_process_tree.group_blocks_in_net(net)
    if len(grouped.transitions) != 1:
        raise UnsupportedModelError(
            "PM4Py could not reduce the process to one structured process-tree transition"
        )
    label = next(iter(grouped.transitions)).label
    if not isinstance(label, str) or not label.strip():
        raise UnsupportedModelError("PM4Py produced an empty reduced process-tree label")
    return _ReducedTreeParser(label, endpoint_to_message).parse()


def load_process_models(
    bpmn_path: Path,
    runtime_dir: Path,
    *,
    normalised_name: str = "collaboration.normalized.bpmn",
) -> tuple[Path, list[ProcessModel]]:


    import pm4py

    normalised = normalise_collaboration(bpmn_path, runtime_dir / normalised_name)
    root = ET.parse(normalised).getroot()
    node_process = _node_to_process(root)

    process_elements = {
        process.get("id"): process
        for process in root.findall(f".//{{{BPMN_NS}}}process")
        if process.get("id")
    }
    participants = root.findall(
        f".//{{{BPMN_NS}}}collaboration/{{{BPMN_NS}}}participant"
    )
    if not participants:
        raise ValueError("BPMN collaboration contains no participants")

    directions: dict[str, tuple[set[str], set[str]]] = {
        process_id: (set(), set()) for process_id in process_elements
    }
    endpoint_to_message: dict[str, str] = {}
    message_names: set[str] = set()
    for flow in root.findall(f".//{{{BPMN_NS}}}messageFlow"):
        raw_name = flow.get("name")
        source = flow.get("sourceRef")
        target = flow.get("targetRef")
        if not raw_name or not source or not target:
            raise ValueError(f"messageFlow {flow.get('id')!r} must have name/sourceRef/targetRef")
        name = _normalise_token(raw_name)
        if name in message_names:
            raise ValueError(
                f"duplicate messageFlow name {name!r}; message activity names must be unique"
            )
        message_names.add(name)
        for endpoint in (source, target):
            if endpoint in endpoint_to_message:
                raise ValueError(
                    f"BPMN node {endpoint!r} participates in multiple message flows; "
                    "one message activity per node is required"
                )
            endpoint_to_message[endpoint] = name
        source_process = node_process.get(source)
        target_process = node_process.get(target)
        if not source_process or not target_process:
            raise ValueError(
                f"messageFlow {flow.get('id')!r} endpoints must belong to participant processes"
            )
        directions[source_process][0].add(name)
        directions[target_process][1].add(name)

    models: list[ProcessModel] = []
    for participant in participants:
        process_id = participant.get("processRef")
        if not process_id or process_id not in process_elements:
            raise ValueError(f"participant {participant.get('id')!r} has invalid processRef")
        participant_name = participant.get("name") or participant.get("id") or process_id
        process_file = runtime_dir / "processes" / f"{process_id}.bpmn"
        _export_process(root, process_id, process_file)
        bpmn_graph = pm4py.read_bpmn(str(process_file))
        conversion_warnings: tuple[str, ...] = ()
        try:
            process_tree = pm4py.convert_to_process_tree(bpmn_graph)
            tree = simplify(_convert_pm4py_tree(process_tree, endpoint_to_message))
        except AssertionError:
            tree = simplify(_convert_reduced_wf_net(bpmn_graph, endpoint_to_message))
            conversion_warnings = (
                "PM4Py reduced the sound WF-net but its generic tree parser failed; "
                "used the seq/XOR/parallel fallback parser.",
            )
        validate_unique_messages(tree)
        sends, receives = directions[process_id]
        models.append(
            ProcessModel(
                process_id=process_id,
                participant_name=participant_name,
                tree=tree,
                sends=frozenset(sends),
                receives=frozenset(receives),
                source_bpmn=normalised,
                process_bpmn=process_file,
                warnings=conversion_warnings,
            )
        )
    return normalised, models
