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

# ============================================================
# BPMN 命名空间（与你文件一致）
# ============================================================

BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}

# ============================================================
# 输出结构：我们仍然复用 SubprocessInfo 名称，
# 但此处“子流程”= participant 对应的 process
# ============================================================

@dataclass
class SubprocessInfo:
    process_id: str
    participant_name: str
    ast: Expr
    MEo: Set[str]                 # 该 process 发送的消息名集合（M1/M2...）
    MEi: Set[str]                 # 该 process 接收的消息名集合（M1/M2...）
    pattern: Set[tuple[str, ...]] # 所有允许的消息序列


# ============================================================
# normalize：把 token 转成 Parser 支持的格式
# 你这里消息名是 M1/M2...，一般无需改，但留着更稳
# ============================================================

def normalize_token(name: str) -> str:
    if name is None:
        return ""
    s = name.strip()
    s = s.replace(" ", "_").replace("-", "_")
    s = re.sub(r"[^0-9A-Za-z_]", "_", s)
    return s


def parse_bpmn_file(path: str) -> ET.ElementTree:
    return ET.parse(path)


# ============================================================
# 1) participant -> process
# 你的 BPMN：collaboration/participant[name, processRef]
# ============================================================

def find_all_participant_processes(root: ET.Element, debug: bool = False) -> List[Tuple[str, str, ET.Element]]:
    """
    返回所有 participant 对应的 process：
      [(participant_name, process_id, process_element), ...]
    """
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


# ============================================================
# 2) node_id -> process_id
# 用于判断 messageFlow 的 sourceRef/targetRef 属于哪个 process
# ============================================================

def build_node_to_process_map(root: ET.Element) -> Dict[str, str]:
    node2proc: Dict[str, str] = {}
    for proc in root.findall(".//bpmn:process", BPMN_NS):
        pid = proc.get("id")
        if not pid:
            continue
        # 把 process 内所有元素（task/event/gateway）登记到所属 process
        for e in proc.iter():
            eid = e.get("id")
            if eid:
                node2proc[eid] = pid
    return node2proc


# ============================================================
# 3) messageFlow 映射：
#  - message_name (M1) -> (source_node_id, target_node_id)
#  - process_id -> (MEo, MEi)
# ============================================================

def build_messageflow_maps(root: ET.Element, debug: bool = False) -> Tuple[
    Dict[str, Tuple[str, str]],                 # msg -> (src_node_id, tgt_node_id)
    Dict[str, Tuple[Set[str], Set[str]]],       # process_id -> (MEo, MEi)
    Dict[str, str],                             # node_id -> process_id
]:
    node2proc = build_node_to_process_map(root)

    # init me_map for all processes
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

        # src process sends token
        if src_proc and src_proc in me_map:
            me_map[src_proc][0].add(token)

        # tgt process receives token
        if tgt_proc and tgt_proc in me_map:
            me_map[tgt_proc][1].add(token)

    return msg2ends, me_map, node2proc


# ============================================================
# 4) 导出每个 process 为独立 BPMN 文件，给 pm4py 使用
#    - 删除 collaboration（messageFlow 等）避免干扰
#    - 只保留一个 process
# ============================================================

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

        # remove collaboration to simplify
        collab = new_root.find(".//bpmn:collaboration", BPMN_NS)
        if collab is not None:
            new_root.remove(collab)

        # remove all process except pid (definitions direct children)
        for ch in list(new_root):
            if ch.tag.endswith("process") and ch.get("id") != pid:
                new_root.remove(ch)

        out_path = os.path.join(out_dir, f"{base}__{pid}.bpmn")
        ET.ElementTree(new_root).write(out_path, encoding="utf-8", xml_declaration=True)
        exported.append((pid, out_path))

        if debug:
            print(f"[EXPORT] process_id={pid} -> {out_path}")

    return exported


# ============================================================
# 5) pm4py process tree -> 论文符号 expr -> AST
# pm4py 常见符号：
#   "->" 顺序
#   "+" AND 并行
#   "X" XOR 选择
# 我们映射为：
#   "." 顺序
#   "|" 并行
#   "+" 选择
# ============================================================

def normalize_pt_string_to_expr_string(pt_str: str) -> str:
    s = pt_str
    s = s.replace("->", ".")
    s = s.replace("'", "")
    s = s.replace(" ", "")
    s = s.replace("+", "|")  # AND -> parallel
    s = s.replace("X", "+")  # XOR -> choice
    return s

def pm4py_bpmn_to_ast(bpmn_file: str, debug: bool = False) -> Tuple[Expr, str, str]:
    """
    返回 (ast, raw_pt_str, normalized_expr_str)
    方便打印调试
    """
    bpmn = pm4py.read_bpmn(bpmn_file)
    pt = pm4py.convert_to_process_tree(bpmn)

    raw = str(pt)
    expr_str = normalize_pt_string_to_expr_string(raw)
    ast = Parser(expr_str).parse()

    if debug:
        print(f"  [PM4PY] raw pt   : {raw}")
        print(f"  [PM4PY] expr str : {expr_str}")

    return ast, raw, expr_str


# ============================================================
# 6) 关键：把 AST 的叶子（任务名 A1/B1...）替换为消息名 M1/M2...
#
# 原因：
#   - 你要做消息模式，因此 token 必须是消息名（messageFlow.name）
#   - pm4py 给的叶子是 task name（A1/B1...），与日志不匹配
#
# 我们用 messageFlow 的 sourceRef/targetRef 找到“哪个任务发/收哪个消息”：
#   sourceRef 节点 -> 视为发送消息 Mx（叶子替换成 Mx）
#   targetRef 节点 -> 视为接收消息 Mx（叶子替换成 Mx）
#
# 注意：
#   pm4py 的叶子名字通常是 task 的 name（如 "A2"），
#   但 messageFlow 里引用的是 task 的 id（如 Activity_04xou70）。
#   所以我们需要：
#     - id -> name 映射（从导出的 process XML）
#     - 再把 “task_name -> message_name” 建出来用于替换
# ============================================================

def build_id_to_name_map_from_bpmn_file(bpmn_file: str) -> Dict[str, str]:
    """
    解析导出的单 process BPMN 文件，构造 id->name 映射
    """
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
    """
    把 AST 叶子（task_name）映射成 message_name（M1...）
    """
    # task_name -> message_name
    taskname_to_msg: Dict[str, str] = {}

    for m, (src_id, tgt_id) in msg2ends.items():
        src_name = id2name.get(src_id)  # e.g., "A2"
        tgt_name = id2name.get(tgt_id)  # e.g., "B2"
        if src_name:
            taskname_to_msg[src_name] = m
        if tgt_name:
            taskname_to_msg[tgt_name] = m

    if debug:
        print(f"  [MAP] taskName -> msgName mapping = {taskname_to_msg}")

    def rec(e: Expr) -> Expr:
        if isinstance(e, Leaf):
            # 如果叶子是 task name，就替换为 message name；否则保留
            new_name = taskname_to_msg.get(normalize_token(e.name), normalize_token(e.name))
            return Leaf(new_name)
        assert isinstance(e, OpNode)
        return OpNode(e.op, tuple(rec(c) for c in e.children))

    return rec(ast)


# ============================================================
# 7) 主入口：对每个 participant/process 计算消息模式
# ============================================================

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

    # participant -> process
    parts = find_all_participant_processes(root, debug=debug)

    # messageFlow maps (global)
    msg2ends, me_map, node2proc = build_messageflow_maps(root, debug=debug)

    # export each process to file for pm4py
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

        # pm4py AST for this process
        ast0, raw_pt, expr_str = pm4py_bpmn_to_ast(proc_file, debug=True)

        # build id->name from the exported single process BPMN
        id2name = build_id_to_name_map_from_bpmn_file(proc_file)

        # rename leaf task names -> message names
        ast = rename_ast_leaves_to_messages(ast0, id2name, msg2ends, debug=True)

        # compute pattern
        pattern = tree_to_mp(ast, MEo, MEi, interleave_limit=interleave_limit)

        # print some patterns for debugging
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