from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Optional, Set, Tuple


def _split_tag(tag: str) -> Tuple[Optional[str], str]:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, local
    return None, tag


def _qname(ns: Optional[str], local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _collect_existing_ids(root: ET.Element) -> Set[str]:
    ids = set()
    for el in root.iter():
        _id = el.attrib.get("id")
        if _id:
            ids.add(_id)
    return ids


def _gen_unique_id(existing: Set[str], base: str) -> str:
    if base not in existing:
        existing.add(base)
        return base
    i = 1
    while f"{base}_{i}" in existing:
        i += 1
    new_id = f"{base}_{i}"
    existing.add(new_id)
    return new_id


def _remove_children_by_local(el: ET.Element, locals_to_remove: Set[str]) -> None:
    for ch in list(el):
        _, l = _split_tag(ch.tag)
        if l in locals_to_remove:
            el.remove(ch)


def _ensure_child_text(el: ET.Element, ns: Optional[str], local: str, text: str) -> None:
    for ch in list(el):
        _, l = _split_tag(ch.tag)
        if l == local and (ch.text or "").strip() == text:
            return
    c = ET.Element(_qname(ns, local))
    c.text = text
    el.append(c)


def _has_child_local(el: ET.Element, local: str) -> bool:
    for ch in list(el):
        _, l = _split_tag(ch.tag)
        if l == local:
            return True
    return False

def _find_first_by_local(root: ET.Element, local: str) -> Optional[ET.Element]:
    for el in root.iter():
        if _split_tag(el.tag)[1] == local:
            return el
    return None


def _find_all_by_local(root: ET.Element, local: str):
    for el in root.iter():
        if _split_tag(el.tag)[1] == local:
            yield el


def _get_attr_ignore_ns(el: ET.Element, attr_name: str) -> Optional[str]:

    return el.attrib.get(attr_name)


def _add_di_for_new_start_and_flow(
    root: ET.Element,
    bpmn_ns: Optional[str],
    start_id: str,
    flow_id: str,
    task_id: str,
) -> None:







    plane = _find_first_by_local(root, "BPMNPlane")
    if plane is None:
        return


    task_shape = None
    for sh in _find_all_by_local(plane, "BPMNShape"):
        if _get_attr_ignore_ns(sh, "bpmnElement") == task_id:
            task_shape = sh
            break
    if task_shape is None:
        return


    bounds = _find_first_by_local(task_shape, "Bounds")
    if bounds is None:
        return


    x = float(bounds.attrib.get("x", "0"))
    y = float(bounds.attrib.get("y", "0"))
    w = float(bounds.attrib.get("width", "100"))
    h = float(bounds.attrib.get("height", "80"))


    start_size = 36.0
    gap = 30.0
    sx = x - gap - start_size
    sy = y + (h - start_size) / 2.0



    di_ns, _ = _split_tag(task_shape.tag)
    dc_ns = None

    dc_ns, _ = _split_tag(bounds.tag)


    start_shape_id = f"{start_id}_di"
    start_shape = ET.Element(_qname(di_ns, "BPMNShape"), {"id": start_shape_id, "bpmnElement": start_id})
    start_bounds = ET.Element(_qname(dc_ns, "Bounds"), {"x": str(sx), "y": str(sy), "width": str(start_size), "height": str(start_size)})
    start_shape.append(start_bounds)
    plane.append(start_shape)


    edge_id = f"{flow_id}_di"
    edge = ET.Element(_qname(di_ns, "BPMNEdge"), {"id": edge_id, "bpmnElement": flow_id})




    waypoint_ns = None
    waypoint_ns = di_ns


    x1 = sx + start_size
    y1 = sy + start_size / 2.0

    x2 = x
    y2 = y + h / 2.0

    wp1 = ET.Element(_qname(waypoint_ns, "waypoint"), {"x": str(x1), "y": str(y1)})
    wp2 = ET.Element(_qname(waypoint_ns, "waypoint"), {"x": str(x2), "y": str(y2)})
    edge.append(wp1)
    edge.append(wp2)
    plane.append(edge)

def _normalize_name_attr(root: ET.Element) -> None:



    for el in root.iter():
        name = el.attrib.get("name")
        if not name:
            continue


        new_name = "_".join(name.strip().split())
        el.attrib["name"] = new_name

def transform_bpmn_events_and_gateways(
    src_bpmn_path: Path,
    out_dir: Path,
    out_name: Optional[str] = None,
) -> Path:













    src_bpmn_path = Path(src_bpmn_path)
    if not src_bpmn_path.exists():
        raise FileNotFoundError(src_bpmn_path)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tree = ET.parse(src_bpmn_path)
    root = tree.getroot()

    bpmn_ns, _ = _split_tag(root.tag)
    existing_ids = _collect_existing_ids(root)

    EVENT_DEFINITION_CHILDREN = {
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


    processes = [el for el in root.iter() if _split_tag(el.tag)[1] == "process"]

    for proc in processes:

        msg_start_events = []

        for el in proc.iter():
            ns, local = _split_tag(el.tag)


            if local == "eventBasedGateway":
                el.tag = _qname(ns, "exclusiveGateway")
                continue


            if local == "startEvent":
                if _has_child_local(el, "messageEventDefinition"):
                    msg_start_events.append(el)
                continue


            if local == "endEvent":
                continue


            if local.endswith("Event"):
                el.tag = _qname(ns, "task")
                _remove_children_by_local(el, EVENT_DEFINITION_CHILDREN)
                continue


        for se in msg_start_events:
            ns, local = _split_tag(se.tag)
            old_id = se.attrib.get("id")
            if not old_id:
                continue


            se.tag = _qname(ns, "task")
            _remove_children_by_local(se, EVENT_DEFINITION_CHILDREN)

            task_id = old_id
            task_name = se.attrib.get("name")


            new_start_id = _gen_unique_id(existing_ids, f"{task_id}_noneStart")
            new_start = ET.Element(_qname(bpmn_ns, "startEvent"), {"id": new_start_id})
            if task_name:
                new_start.attrib["name"] = f"Start_{task_name}"


            new_flow_id = _gen_unique_id(existing_ids, f"{task_id}_noneStartFlow")
            new_flow = ET.Element(
                _qname(bpmn_ns, "sequenceFlow"),
                {"id": new_flow_id, "sourceRef": new_start_id, "targetRef": task_id},
            )


            _ensure_child_text(new_start, bpmn_ns, "outgoing", new_flow_id)
            _ensure_child_text(se, bpmn_ns, "incoming", new_flow_id)


            proc.append(new_start)
            proc.append(new_flow)
            _add_di_for_new_start_and_flow(
                root=root,
                bpmn_ns=bpmn_ns,
                start_id=new_start_id,
                flow_id=new_flow_id,
                task_id=task_id,
            )


    _normalize_name_attr(root)

    if out_name is None:
        out_name = src_bpmn_path.stem + ".normalized.bpmn"
    out_path = out_dir / out_name
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
