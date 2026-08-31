from __future__ import annotations

import os
import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Tuple, Set, Optional, Dict

import pm4py

from ast_expr import Expr, Parser, Leaf, OpNode
from mp_patterns import tree_to_mp





BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}






@dataclass
class SubprocessInfo:
    process_id: str
    participant_name: str
    ast: Expr
    MEo: Set[str]
    MEi: Set[str]
    pattern: Set[tuple[str, ...]]







def normalize_token(name: str) -> str:
    if name is None:
        return ""
    s = name.strip()
    s = s.replace(" ", "_").replace("-", "_")
    s = re.sub(r"[^0-9A-Za-z_]", "_", s)
    return s


def parse_bpmn_file(path: str) -> ET.ElementTree:
    return ET.parse(path)







def find_all_participant_processes(root: ET.Element, debug: bool = False) -> List[Tuple[str, str, ET.Element]]:




    proc_map = {p.get("id"): p for p in root.findall(".//bpmn:process", BPMN_NS)}

    res: List[Tuple[str, str, ET.Element]] = []
    parts = root.findall(".//bpmn:collaboration/bpmn:participant", BPMN_NS)
    if debug:
        print(f"[DISCOVER] participant_count={len(parts)} process_count={len(proc_map)}")

    for part in parts:
        pname = part.get("name") or part.get("id") or "UnnamedParticipant"
        pref = part.get("processRef")
        if pref and pref in proc_map:
            res.append((pname, pref, proc_map[pref]))
            if debug:
                print(f"  participant='{pname}' -> processRef='{pref}'")
        else:
            if debug:
                print(f"  participant='{pname}' has invalid/missing processRef='{pref}'")

    return res







def build_node_to_process_map(root: ET.Element) -> Dict[str, str]:
    node2proc: Dict[str, str] = {}
    for proc in root.findall(".//bpmn:process", BPMN_NS):
        pid = proc.get("id")
        if not pid:
            continue

        for e in proc.iter():
            eid = e.get("id")
            if eid:
                node2proc[eid] = pid
    return node2proc








def build_messageflow_maps(root: ET.Element, debug: bool = False) -> Tuple[
    Dict[str, Tuple[str, str]],
    Dict[str, Tuple[Set[str], Set[str]]],
    Dict[str, str],
]:
    node2proc = build_node_to_process_map(root)


    proc_ids = [p.get("id") for p in root.findall(".//bpmn:process", BPMN_NS) if p.get("id")]
    me_map: Dict[str, Tuple[Set[str], Set[str]]] = {pid: (set(), set()) for pid in proc_ids}

    msg2ends: Dict[str, Tuple[str, str]] = {}

    mflows = root.findall(".//bpmn:messageFlow", BPMN_NS)
    if debug:
        print(f"[MSGFLOW] messageFlow_count={len(mflows)}")

    for mf in mflows:
        mname = mf.get("name")
        src = mf.get("sourceRef")
        tgt = mf.get("targetRef")
        if not (mname and src and tgt):
            if debug:
                print("  [MSGFLOW] skip flow missing name/src/tgt")
            continue

        token = normalize_token(mname)
        msg2ends[token] = (src, tgt)

        src_proc = node2proc.get(src)
        tgt_proc = node2proc.get(tgt)

        if debug:
            print(f"  [MSGFLOW] {token}: {src}({src_proc}) -> {tgt}({tgt_proc})")


        if src_proc and src_proc in me_map:
            me_map[src_proc][0].add(token)


        if tgt_proc and tgt_proc in me_map:
            me_map[tgt_proc][1].add(token)

    return msg2ends, me_map, node2proc








def export_processes_to_bpmn_files(original_bpmn_path: str, out_dir: str, debug: bool = False) -> List[Tuple[str, str]]:
    os.makedirs(out_dir, exist_ok=True)
    tree = ET.parse(original_bpmn_path)
    root = tree.getroot()

    procs = root.findall(".//bpmn:process", BPMN_NS)
    base = os.path.splitext(os.path.basename(original_bpmn_path))[0]

    exported: List[Tuple[str, str]] = []

    for p in procs:
        pid = p.get("id")
        if not pid:
            continue

        new_root = copy.deepcopy(root)


        collab = new_root.find(".//bpmn:collaboration", BPMN_NS)
        if collab is not None:
            new_root.remove(collab)


        for ch in list(new_root):
            if ch.tag.endswith("process") and ch.get("id") != pid:
                new_root.remove(ch)

        out_path = os.path.join(out_dir, f"{base}__{pid}.bpmn")
        ET.ElementTree(new_root).write(out_path, encoding="utf-8", xml_declaration=True)
        exported.append((pid, out_path))

        if debug:
            print(f"[EXPORT] process_id={pid} -> {out_path}")

    return exported














def normalize_pt_string_to_expr_string(pt_str: str) -> str:
    s = pt_str
    s = s.replace("->", ".")
    s = s.replace("'", "")
    s = s.replace(" ", "")
    s = s.replace("+", "|")
    s = s.replace("X", "+")
    return s

def pm4py_bpmn_to_ast(bpmn_file: str, debug: bool = False) -> Tuple[Expr, str, str]:




    bpmn = pm4py.read_bpmn(bpmn_file)
    pt = pm4py.convert_to_process_tree(bpmn)

    raw = str(pt)
    expr_str = normalize_pt_string_to_expr_string(raw)
    ast = Parser(expr_str).parse()

    if debug:
        print(f"  [PM4PY] raw pt   : {raw}")
        print(f"  [PM4PY] expr str : {expr_str}")

    return ast, raw, expr_str





















def build_id_to_name_map_from_bpmn_file(bpmn_file: str) -> Dict[str, str]:



    root = ET.parse(bpmn_file).getroot()
    id2name: Dict[str, str] = {}
    for e in root.iter():
        eid = e.get("id")
        nm = e.get("name")
        if eid and nm:
            id2name[eid] = normalize_token(nm)
    return id2name

def rename_ast_leaves_to_messages(
    ast: Expr,
    id2name: Dict[str, str],
    msg2ends: Dict[str, Tuple[str, str]],
    debug: bool = False
) -> Expr:




    taskname_to_msg: Dict[str, str] = {}

    for m, (src_id, tgt_id) in msg2ends.items():
        src_name = id2name.get(src_id)
        tgt_name = id2name.get(tgt_id)
        if src_name:
            taskname_to_msg[src_name] = m
        if tgt_name:
            taskname_to_msg[tgt_name] = m

    if debug:
        print(f"  [MAP] taskName -> msgName mapping = {taskname_to_msg}")

    def rec(e: Expr) -> Expr:
        if isinstance(e, Leaf):

            new_name = taskname_to_msg.get(normalize_token(e.name), normalize_token(e.name))
            return Leaf(new_name)
        assert isinstance(e, OpNode)
        return OpNode(e.op, tuple(rec(c) for c in e.children))

    return rec(ast)






def compute_all_process_patterns(
    bpmn_path: str,
    tmp_dir: str = "_tmp_process_bpmns",
    interleave_limit: Optional[int] = 2000,
    debug: bool = True
) -> List[SubprocessInfo]:

    xml_tree = parse_bpmn_file(bpmn_path)
    root = xml_tree.getroot()

    if debug:
        print("=" * 80)
        print(f"[LOAD] BPMN file = {bpmn_path}")
        print(f"[LOAD] root.tag  = {root.tag}")


    parts = find_all_participant_processes(root, debug=debug)


    msg2ends, me_map, node2proc = build_messageflow_maps(root, debug=debug)


    exported = export_processes_to_bpmn_files(bpmn_path, tmp_dir, debug=debug)
    exported_map = {pid: path for pid, path in exported}

    infos: List[SubprocessInfo] = []

    for idx, (pname, pid, _) in enumerate(parts, 1):
        print("-" * 80)
        print(f"[PROC] ({idx}/{len(parts)}) participant='{pname}' process_id='{pid}'")

        MEo, MEi = me_map.get(pid, (set(), set()))
        print(f"  [MEo] send messages = {sorted(MEo)}")
        print(f"  [MEi] recv messages = {sorted(MEi)}")

        if pid not in exported_map:
            print(f"  [WARN] exported BPMN for process_id='{pid}' not found, skip")
            continue

        proc_file = exported_map[pid]


        ast0, raw_pt, expr_str = pm4py_bpmn_to_ast(proc_file, debug=True)


        id2name = build_id_to_name_map_from_bpmn_file(proc_file)


        ast = rename_ast_leaves_to_messages(ast0, id2name, msg2ends, debug=True)


        pattern = tree_to_mp(ast, MEo, MEi, interleave_limit=interleave_limit)


        sample = sorted(pattern, key=lambda x: (len(x), x))[:30]
        print(f"  [PATTERN] size={len(pattern)} sample_first_30={sample}")

        infos.append(SubprocessInfo(
            process_id=pid,
            participant_name=pname,
            ast=ast,
            MEo=MEo,
            MEi=MEi,
            pattern=pattern
        ))

    print("=" * 80)
    print(f"[DONE] computed patterns for {len(infos)} processes")
    return infos
