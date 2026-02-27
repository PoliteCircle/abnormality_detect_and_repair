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
    # 兼容 ET 把 attr 带命名空间的情况（一般 bpmnElement 不带 ns）
    return el.attrib.get(attr_name)


def _add_di_for_new_start_and_flow(
    root: ET.Element,
    bpmn_ns: Optional[str],
    start_id: str,
    flow_id: str,
    task_id: str,
) -> None:
    """
    给新增的 startEvent 和 sequenceFlow 补 BPMNDI：
      - 新增 BPMNShape(start)
      - 新增 BPMNEdge(flow) + waypoint
    如果找不到 BPMNDI 或找不到 task 的 shape，则跳过（不报错）。
    """
    # 找 BPMNPlane（里面放 shapes/edges）
    plane = _find_first_by_local(root, "BPMNPlane")
    if plane is None:
        return

    # 找到 task 的 shape（bpmnElement == task_id）
    task_shape = None
    for sh in _find_all_by_local(plane, "BPMNShape"):
        if _get_attr_ignore_ns(sh, "bpmnElement") == task_id:
            task_shape = sh
            break
    if task_shape is None:
        return

    # 找 task_shape 的 Bounds
    bounds = _find_first_by_local(task_shape, "Bounds")
    if bounds is None:
        return

    # Bounds 属于 dc 命名空间，但我们只读 attrib
    x = float(bounds.attrib.get("x", "0"))
    y = float(bounds.attrib.get("y", "0"))
    w = float(bounds.attrib.get("width", "100"))
    h = float(bounds.attrib.get("height", "80"))

    # 新 start 事件放到 task 左边
    start_size = 36.0
    gap = 30.0
    sx = x - gap - start_size
    sy = y + (h - start_size) / 2.0

    # 取到 DI / DC 的 namespace（从现有元素推断）
    # 例：{http://www.omg.org/spec/BPMN/20100524/DI}BPMNShape
    di_ns, _ = _split_tag(task_shape.tag)  # 这是 bpmndi 的 ns
    dc_ns = None
    # bounds.tag 类似 {http://www.omg.org/spec/DD/20100524/DC}Bounds
    dc_ns, _ = _split_tag(bounds.tag)

    # 新增 start shape
    start_shape_id = f"{start_id}_di"
    start_shape = ET.Element(_qname(di_ns, "BPMNShape"), {"id": start_shape_id, "bpmnElement": start_id})
    start_bounds = ET.Element(_qname(dc_ns, "Bounds"), {"x": str(sx), "y": str(sy), "width": str(start_size), "height": str(start_size)})
    start_shape.append(start_bounds)
    plane.append(start_shape)

    # 新增 flow edge
    edge_id = f"{flow_id}_di"
    edge = ET.Element(_qname(di_ns, "BPMNEdge"), {"id": edge_id, "bpmnElement": flow_id})

    # waypoint 属于 DI 命名空间（DD/DI），但通常与 BPMNEdge 同 ns 系
    # 例：{http://www.omg.org/spec/DD/20100524/DI}waypoint
    # 这里用 edge 的 ns 也能工作；若你的文件严格区分，可再推断一次
    waypoint_ns = None
    waypoint_ns = di_ns  # 大多数工具兼容；如不行我再给你严格版

    # 起点：start 右侧中心
    x1 = sx + start_size
    y1 = sy + start_size / 2.0
    # 终点：task 左侧中心
    x2 = x
    y2 = y + h / 2.0

    wp1 = ET.Element(_qname(waypoint_ns, "waypoint"), {"x": str(x1), "y": str(y1)})
    wp2 = ET.Element(_qname(waypoint_ns, "waypoint"), {"x": str(x2), "y": str(y2)})
    edge.append(wp1)
    edge.append(wp2)
    plane.append(edge)

def _normalize_name_attr(root: ET.Element) -> None:
    """
    将所有元素的 name 属性中的空格替换为下划线，并去掉首尾空格
    """
    for el in root.iter():
        name = el.attrib.get("name")
        if not name:
            continue

        # 多空格 -> 单空格 -> 下划线
        new_name = "_".join(name.strip().split())
        el.attrib["name"] = new_name

def transform_bpmn_events_and_gateways(
    src_bpmn_path: Path,
    out_dir: Path,
    out_name: Optional[str] = None,
) -> Path:
    """
    转换规则（按你的最新要求）：
      1) eventBasedGateway -> exclusiveGateway（id/name 保留）
      2) “消息开始事件”（startEvent 且包含 messageEventDefinition）：
         - 把该 startEvent 改为同名 task（id/name 保留）
         - 在其前面新增一个普通 startEvent（none start）
         - 新增一条 sequenceFlow: newStart -> task
         - 原有 messageFlow / sequenceFlow 都不改动（仅新增这条 sequenceFlow）
      3) 普通 startEvent 保持 startEvent（圆形）
      4) endEvent 保持 endEvent（深色圆形）
      5) 其他事件（intermediate/boundary 等）-> 同名 task（id/name 保留）
      6) 不处理 BPMNDI（布局）
    """
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

    # 找到所有 process
    processes = [el for el in root.iter() if _split_tag(el.tag)[1] == "process"]

    for proc in processes:
        # 先改网关/其他事件；对“消息开始事件”需要新增节点，所以单独收集后处理更安全
        msg_start_events = []

        for el in proc.iter():
            ns, local = _split_tag(el.tag)

            # 1) eventBasedGateway -> exclusiveGateway
            if local == "eventBasedGateway":
                el.tag = _qname(ns, "exclusiveGateway")
                continue

            # 2) startEvent：区分消息开始事件 vs 普通开始事件
            if local == "startEvent":
                if _has_child_local(el, "messageEventDefinition"):
                    msg_start_events.append(el)  # 之后统一处理
                continue

            # 3) endEvent 保持不变
            if local == "endEvent":
                continue

            # 4) 其他事件 -> task
            if local.endswith("Event"):
                el.tag = _qname(ns, "task")
                _remove_children_by_local(el, EVENT_DEFINITION_CHILDREN)
                continue

        # 现在处理“消息开始事件”：改成 task + 新增 none start + 新增 flow
        for se in msg_start_events:
            ns, local = _split_tag(se.tag)
            old_id = se.attrib.get("id")
            if not old_id:
                continue

            # 把该 startEvent 改为 task（id/name 保留）
            se.tag = _qname(ns, "task")
            _remove_children_by_local(se, EVENT_DEFINITION_CHILDREN)

            task_id = old_id
            task_name = se.attrib.get("name")

            # 新增普通 startEvent（none start）
            new_start_id = _gen_unique_id(existing_ids, f"{task_id}_noneStart")
            new_start = ET.Element(_qname(bpmn_ns, "startEvent"), {"id": new_start_id})
            if task_name:
                new_start.attrib["name"] = f"Start_{task_name}"

            # 新增 sequenceFlow: new_start -> task
            new_flow_id = _gen_unique_id(existing_ids, f"{task_id}_noneStartFlow")
            new_flow = ET.Element(
                _qname(bpmn_ns, "sequenceFlow"),
                {"id": new_flow_id, "sourceRef": new_start_id, "targetRef": task_id},
            )

            # 维护 incoming/outgoing（只“加”，不动原有的）
            _ensure_child_text(new_start, bpmn_ns, "outgoing", new_flow_id)
            _ensure_child_text(se, bpmn_ns, "incoming", new_flow_id)

            # 挂进 process
            proc.append(new_start)
            proc.append(new_flow)
            _add_di_for_new_start_and_flow(
                root=root,
                bpmn_ns=bpmn_ns,
                start_id=new_start_id,
                flow_id=new_flow_id,
                task_id=task_id,
            )

    # === 统一清洗所有 name ===
    _normalize_name_attr(root)

    if out_name is None:
        out_name = src_bpmn_path.stem + ".normalized.bpmn"
    out_path = out_dir / out_name
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path