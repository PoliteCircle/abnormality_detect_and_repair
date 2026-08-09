from dataclasses import dataclass, field
from typing import List, Set, Optional, Dict, Tuple


# ============================================================
# 1. 表达式树节点、失败节点、匹配结果、修复候选定义
# ============================================================

@dataclass
class Node:
    """
    消息模式表达式树节点。

    op:
        tau     : 空动作 τ
        recv    : 接收消息，如 ?A
        send    : 发送消息，如 !B
        seq     : 顺序结构，如 XY
        choice  : 排他/选择结构，如 X+Y
        par     : 并行结构，如 X||Y

    msg:
        叶子节点对应的消息名，例如 ?A 和 !A 的 msg 都是 A。

    children:
        非叶子节点的子节点列表。

    messages:
        当前子树中包含的所有消息名，自底向上计算。

    mapped_traces:
        自顶向下映射到该节点的日志序列集合。
        单条 trace 情况下通常只有一个元素，例如 [['A', 'B']]。

    unmapped_traces:
        对当前节点来说，无法映射到任何子节点的消息。
        主要用于发现“模型中不存在的新消息”。
    """
    op: str
    msg: Optional[str] = None
    children: List["Node"] = field(default_factory=list)
    messages: Set[str] = field(default_factory=set)
    mapped_traces: List[List[str]] = field(default_factory=list)
    unmapped_traces: List[List[str]] = field(default_factory=list)

    def label(self) -> str:
        if self.op == "tau":
            return "τ"
        if self.op == "recv":
            return f"?{self.msg}"
        if self.op == "send":
            return f"!{self.msg}"
        if self.op == "seq":
            return "SEQ"
        if self.op == "choice":
            return "XOR(+)"
        if self.op == "par":
            return "PAR(||)"
        return self.op


@dataclass
class FailedNode:
    """
    失败匹配节点。

    本版本只记录最终返回的、尽量靠近树底的失败节点。
    """
    path: str
    label: str
    expected_messages: List[str]
    assigned_trace: str
    reason: str


@dataclass
class MatchResult:
    """
    匹配结果。

    matched=True, blocked=False:
        匹配成功，开放完成。

    matched=True, blocked=True:
        匹配成功，但处于合法阻塞。

    matched=False:
        匹配失败，并返回失败节点。
    """
    matched: bool
    blocked: bool
    failed_nodes: List[FailedNode] = field(default_factory=list)

    def text(self) -> str:
        if not self.matched:
            return "匹配失败"
        if self.blocked:
            return "匹配成功，但已经阻塞"
        return "匹配成功，且未阻塞"


@dataclass
class RepairCandidate:
    """
    一个候选修复方案。
    """
    repaired_subtree: Node
    cost: int
    reason: str


@dataclass
class RepairAttempt:
    """
    对某个失败节点或其祖先结构进行一次修复尝试后的结果。
    """
    failed_path: str
    failed_label: str
    original_subtree_expr: str
    repaired_subtree_expr: str
    repaired_whole_expr: str
    cost: int
    reason: str
    rematch_result: MatchResult


# ============================================================
# 2. 基础工具函数
# ============================================================

def trace_to_list(trace: str) -> List[str]:
    """
    将输入消息迹字符串转换为列表。

    例如：
        ABD -> ['A', 'B', 'D']
    """
    return list(trace)


def format_trace(trace: List[str]) -> str:
    """
    美化输出单条消息迹。
    """
    return "ε" if not trace else "".join(trace)


def format_trace_set(traces: List[List[str]]) -> str:
    """
    美化输出日志集合。
    """
    if not traces:
        return "{}"
    return "{" + ", ".join(format_trace(t) for t in traces) + "}"


def normalize_traces(traces: List[List[str]]) -> List[List[str]]:
    """
    去重并保持顺序，避免日志集合中重复片段过多影响输出。
    """
    seen = set()
    out = []
    for t in traces:
        key = tuple(t)
        if key not in seen:
            seen.add(key)
            out.append(list(t))
    return out


def make_failed_node(node: Node, path: str, trace: List[str], reason: str) -> FailedNode:
    """
    构造失败节点记录。
    """
    return FailedNode(
        path=path,
        label=node.label(),
        expected_messages=sorted(node.messages),
        assigned_trace=format_trace(trace),
        reason=reason
    )


def print_failed_nodes(failed_nodes: List[FailedNode]) -> None:
    """
    打印失败节点。
    """
    if not failed_nodes:
        print("无失败匹配节点。")
        return

    print("失败匹配节点列表：")
    for i, fn in enumerate(failed_nodes, start=1):
        print(f"  [{i}] path={fn.path}")
        print(f"      节点={fn.label}")
        print(f"      节点消息集合={fn.expected_messages}")
        print(f"      分配到的消息迹={fn.assigned_trace}")
        print(f"      失败原因={fn.reason}")


def leaf_from_message(msg: str, default_op: str = "send") -> Node:
    """
    根据消息名构造叶子节点。

    由于当前日志中只有消息名，没有 ? / ! 方向，
    默认把未知消息修复为发送 !m。

    如果你的日志中能区分发送/接收，可以在这里改成相应规则。
    """
    return Node(op=default_op, msg=msg)


def clone_node(node: Node) -> Node:
    """
    深拷贝节点，但不保留 mapped_traces / unmapped_traces。
    修复后会重新计算 messages 和映射日志。
    """
    new_node = Node(op=node.op, msg=node.msg)
    new_node.children = [clone_node(c) for c in node.children]
    return new_node


def node_to_expr(node: Node) -> str:
    """
    把表达式树转回字符串表达式，用于展示修复结果。
    """
    if node.op == "tau":
        return "τ"

    if node.op == "recv":
        return f"?{node.msg}"

    if node.op == "send":
        return f"!{node.msg}"

    if node.op == "seq":
        parts = []
        for c in node.children:
            s = node_to_expr(c)
            if c.op in ["choice", "par"]:
                s = f"({s})"
            parts.append(s)
        return "".join(parts)

    if node.op == "choice":
        parts = []
        for c in node.children:
            s = node_to_expr(c)
            if c.op == "choice":
                s = f"({s})"
            parts.append(s)
        return "+".join(parts)

    if node.op == "par":
        parts = []
        for c in node.children:
            s = node_to_expr(c)
            if c.op in ["choice", "seq"]:
                s = f"({s})"
            parts.append(s)
        return "||".join(parts)

    return node.label()


def simplify_node(node: Node) -> Node:
    """
    简化修复后的表达式树：

    1. 去掉 SEQ 中的 τ；
    2. 把只有一个孩子的 SEQ / choice / par 压缩为该孩子；
    3. 递归简化子节点。
    """
    if node.op in ["tau", "recv", "send"]:
        return clone_node(node)

    children = [simplify_node(c) for c in node.children]

    if node.op == "seq":
        children = [c for c in children if c.op != "tau"]

        if not children:
            return Node(op="tau")

        if len(children) == 1:
            return children[0]

        return Node(op="seq", children=children)

    if node.op in ["choice", "par"]:
        if len(children) == 0:
            return Node(op="tau")

        if len(children) == 1:
            return children[0]

        return Node(op=node.op, children=children)

    return Node(op=node.op, children=children)


def prepared(node: Node) -> Node:
    """
    简化节点并重新计算 messages。
    修复生成的新节点一定要调用这个函数，否则 messages 为空会影响后续关系分析。
    """
    n = simplify_node(node)
    compute_messages(n)
    return n


# ============================================================
# 3. 词法分析
# ============================================================

def tokenize(expr: str) -> List[str]:
    """
    将表达式字符串拆分为 token。

    支持：
        ?A
        !B
        +
        ||
        (
        )
        τ
        ε
        tau
    """
    tokens = []
    i = 0

    while i < len(expr):
        ch = expr[i]

        if ch.isspace():
            i += 1
            continue

        if ch in "()+":
            tokens.append(ch)
            i += 1
            continue

        if ch in ["τ", "ε"]:
            tokens.append("τ")
            i += 1
            continue

        if expr.startswith("tau", i):
            tokens.append("τ")
            i += 3
            continue

        if expr.startswith("||", i):
            tokens.append("||")
            i += 2
            continue

        if ch in "?!":
            prefix = ch
            i += 1

            if i >= len(expr) or not expr[i].isalnum():
                raise ValueError(f"消息名前缀 {prefix} 后面缺少消息名")

            name = []
            while i < len(expr) and expr[i].isalnum():
                name.append(expr[i])
                i += 1

            tokens.append(prefix + "".join(name))
            continue

        raise ValueError(f"无法识别的字符: {ch}")

    return tokens


# ============================================================
# 4. 递归下降解析器
# ============================================================

class Parser:
    """
    解析优先级：

        1. 括号
        2. 顺序连接
        3. 并行 ||
        4. 选择 +
    """

    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.pos = 0

    def current(self) -> Optional[str]:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def consume(self, expected: Optional[str] = None) -> str:
        tok = self.current()

        if tok is None:
            raise ValueError("表达式提前结束")

        if expected is not None and tok != expected:
            raise ValueError(f"期望 token {expected}, 实际得到 {tok}")

        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self.parse_choice()

        if self.current() is not None:
            raise ValueError(f"表达式末尾存在多余 token: {self.current()}")

        compute_messages(node)
        return node

    def parse_choice(self) -> Node:
        """
        choice ::= parallel ('+' parallel)*
        """
        nodes = [self.parse_parallel()]

        while self.current() == "+":
            self.consume("+")
            nodes.append(self.parse_parallel())

        if len(nodes) == 1:
            return nodes[0]

        return Node(op="choice", children=nodes)

    def parse_parallel(self) -> Node:
        """
        parallel ::= sequence ('||' sequence)*
        """
        nodes = [self.parse_sequence()]

        while self.current() == "||":
            self.consume("||")
            nodes.append(self.parse_sequence())

        if len(nodes) == 1:
            return nodes[0]

        return Node(op="par", children=nodes)

    def parse_sequence(self) -> Node:
        """
        sequence ::= factor+

        顺序结构是隐式的，例如：
            ?A!B!C
        会被解析为：
            SEQ(?A, !B, !C)
        """
        nodes = []

        while True:
            tok = self.current()

            if tok is None or tok in [")", "+", "||"]:
                break

            nodes.append(self.parse_factor())

        if not nodes:
            raise ValueError("缺少顺序结构中的元素")

        if len(nodes) == 1:
            return nodes[0]

        return Node(op="seq", children=nodes)

    def parse_factor(self) -> Node:
        tok = self.current()

        if tok == "(":
            self.consume("(")
            node = self.parse_choice()
            self.consume(")")
            return node

        if tok == "τ":
            self.consume()
            return Node(op="tau")

        if tok is not None and tok.startswith("?"):
            self.consume()
            return Node(op="recv", msg=tok[1:])

        if tok is not None and tok.startswith("!"):
            self.consume()
            return Node(op="send", msg=tok[1:])

        raise ValueError(f"无法解析 factor: {tok}")


# ============================================================
# 5. 自底向上计算 messages
# ============================================================

def compute_messages(node: Node) -> Set[str]:
    """
    自底向上计算每个节点的消息集合。
    """
    if node.op == "tau":
        node.messages = set()

    elif node.op in ["recv", "send"]:
        node.messages = {node.msg}

    else:
        msg_set = set()
        for child in node.children:
            msg_set |= compute_messages(child)
        node.messages = msg_set

    return node.messages


def print_tree(node: Node, indent: int = 0, path: str = "root") -> None:
    """
    打印表达式树结构、消息集合、映射日志和未映射日志。
    """
    prefix = "  " * indent
    print(
        f"{prefix}- path={path}, {node.label()}, "
        f"messages={sorted(node.messages)}, "
        f"mapped={format_trace_set(node.mapped_traces)}, "
        f"unmapped={format_trace_set(node.unmapped_traces)}"
    )

    for idx, child in enumerate(node.children, start=1):
        print_tree(child, indent + 1, f"{path}/{idx}")


# ============================================================
# 6. 自顶向下计算每个子树的映射日志序列
# ============================================================

def project_trace(trace: List[str], messages: Set[str]) -> List[str]:
    """
    将 trace 投影到某个消息集合上。
    """
    return [m for m in trace if m in messages]


def unmapped_part(trace: List[str], child_message_sets: List[Set[str]]) -> List[str]:
    """
    当前节点的 trace 中，无法映射到任何子节点的消息。
    """
    union_messages = set()
    for s in child_message_sets:
        union_messages |= s

    return [m for m in trace if m not in union_messages]


def reset_mapped_traces(node: Node) -> None:
    """
    清空整棵树上的 mapped_traces / unmapped_traces。
    """
    node.mapped_traces = []
    node.unmapped_traces = []

    for c in node.children:
        reset_mapped_traces(c)


def compute_mapped_traces_top_down(
    node: Node,
    traces: List[List[str]],
    indent: int = 0,
    path: str = "root"
) -> None:
    """
    自顶向下计算每个子树对应的映射日志序列。

    这里使用“投影”而不是匹配时的贪心划分：

        - 对任意子节点 Ti，把当前日志片段 tr 中属于 messages(Ti) 的消息保留下来；
        - 这样可以发现顺序反转、排他分支同时出现、并行分支退化等问题；
        - 对嵌套结构也能继续递归计算局部日志。

    注意：
        mapped_traces 用于修复分析；
        真正的匹配仍然使用 match() 中的匹配语义。
    """
    prefix = "  " * indent

    node.mapped_traces = normalize_traces(traces)

    print(
        f"{prefix}映射节点 {node.label()} path={path}："
        f"mapped={format_trace_set(node.mapped_traces)}"
    )

    if node.op in ["tau", "recv", "send"]:
        return

    child_message_sets = [c.messages for c in node.children]

    node.unmapped_traces = normalize_traces([
        unmapped_part(tr, child_message_sets) for tr in traces
    ])

    if any(len(u) > 0 for u in node.unmapped_traces):
        print(
            f"{prefix}  注意：该节点存在无法映射到任何子节点的消息："
            f"{format_trace_set(node.unmapped_traces)}"
        )

    child_traces: List[List[List[str]]] = [[] for _ in node.children]

    for tr in traces:
        for idx, child in enumerate(node.children):
            child_traces[idx].append(project_trace(tr, child.messages))

    for idx, child in enumerate(node.children, start=1):
        compute_mapped_traces_top_down(
            child,
            normalize_traces(child_traces[idx - 1]),
            indent + 1,
            f"{path}/{idx}"
        )


# ============================================================
# 7. 匹配阶段使用的划分函数
# ============================================================

def split_for_sequence(children: List[Node], trace: List[str]) -> List[List[str]]:
    """
    顺序结构的匹配划分。

    从左到右根据每个子树的 messages 尽可能消费。
    """
    segments = []
    pos = 0

    for child in children:
        seg = []

        while pos < len(trace) and trace[pos] in child.messages:
            seg.append(trace[pos])
            pos += 1

        segments.append(seg)

    if pos < len(trace):
        if segments:
            segments[-1].extend(trace[pos:])
        else:
            segments.append(trace[pos:])

    return segments


def split_for_parallel(children: List[Node], trace: List[str]) -> Optional[List[List[str]]]:
    """
    并行结构的匹配划分。

    按消息集合把 trace 投影到对应分支。
    """
    segments = [[] for _ in children]

    for m in trace:
        candidates = []

        for idx, child in enumerate(children):
            if m in child.messages:
                candidates.append(idx)

        if len(candidates) == 0:
            return None

        if len(candidates) > 1:
            raise ValueError(
                f"消息 {m} 同时属于多个并行分支，存在歧义。"
                f"建议使用活动ID+消息名作为唯一符号。"
            )

        segments[candidates[0]].append(m)

    return segments


# ============================================================
# 8. 核心匹配算法：只返回更靠近树底的失败节点
# ============================================================

def match(
    node: Node,
    trace: List[str],
    indent: int = 0,
    path: str = "root",
    verbose: bool = True
) -> MatchResult:
    """
    递归匹配消息迹 trace 是否属于当前子表达式 node 的消息模式。

    verbose=False 时用于修复后的重新匹配，不输出大量过程。
    """
    prefix = "  " * indent

    if verbose:
        print(
            f"{prefix}进入节点 {node.label()}，"
            f"path={path}，"
            f"节点消息集合={sorted(node.messages)}，"
            f"待匹配消息迹={format_trace(trace)}"
        )

    # ------------------------------------------------------------
    # 8.1 空动作 τ
    # ------------------------------------------------------------
    if node.op == "tau":
        if len(trace) == 0:
            result = MatchResult(matched=True, blocked=False)
        else:
            reason = f"空动作 τ 只能匹配 ε，但实际分配到 {format_trace(trace)}"
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[make_failed_node(node, path, trace, reason)]
            )

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    # ------------------------------------------------------------
    # 8.2 接收消息 ?m
    # ------------------------------------------------------------
    if node.op == "recv":
        if len(trace) == 0:
            if verbose:
                print(f"{prefix}  接收节点 ?{node.msg} 匹配 ε：消息未到达，形成合法阻塞前缀")
            result = MatchResult(matched=True, blocked=True)

        elif len(trace) == 1 and trace[0] == node.msg:
            if verbose:
                print(f"{prefix}  接收节点 ?{node.msg} 匹配 {trace[0]}：成功接收，未阻塞")
            result = MatchResult(matched=True, blocked=False)

        else:
            reason = (
                f"接收节点 ?{node.msg} 只能匹配 ε 或 {node.msg}，"
                f"但实际分配到 {format_trace(trace)}"
            )
            if verbose:
                print(f"{prefix}  {reason}")
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[make_failed_node(node, path, trace, reason)]
            )

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    # ------------------------------------------------------------
    # 8.3 发送消息 !m
    # ------------------------------------------------------------
    if node.op == "send":
        if len(trace) == 1 and trace[0] == node.msg:
            if verbose:
                print(f"{prefix}  发送节点 !{node.msg} 匹配 {trace[0]}：成功发送，未阻塞")
            result = MatchResult(matched=True, blocked=False)

        else:
            if len(trace) == 0:
                reason = f"发送节点 !{node.msg} 不能匹配 ε，发送动作缺失"
            else:
                reason = (
                    f"发送节点 !{node.msg} 只能匹配 {node.msg}，"
                    f"但实际分配到 {format_trace(trace)}"
                )

            if verbose:
                print(f"{prefix}  {reason}")

            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[make_failed_node(node, path, trace, reason)]
            )

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    # ------------------------------------------------------------
    # 8.4 顺序结构
    # ------------------------------------------------------------
    if node.op == "seq":
        if verbose:
            print(f"{prefix}  当前节点是顺序结构，需要从左到右依次匹配各个子结构")

        segments = split_for_sequence(node.children, trace)

        if verbose:
            print(f"{prefix}  根据各子树消息集合进行初步匹配划分：")
            for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
                print(
                    f"{prefix}    第 {idx} 个子节点 path={path}/{idx}，"
                    f"{child.label()}，"
                    f"消息集合={sorted(child.messages)}，"
                    f"初步分配消息迹={format_trace(seg)}"
                )

        already_blocked = False

        for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
            child_path = f"{path}/{idx}"

            if verbose:
                print(f"{prefix}  开始匹配第 {idx} 个顺序子结构：{child.label()}")

            if already_blocked:
                if len(seg) == 0:
                    if verbose:
                        print(f"{prefix}    前序结构已经阻塞，当前子结构分配到 ε，保持阻塞")
                    continue

                reason = (
                    f"前序结构已经阻塞，但当前子结构 {child.label()} "
                    f"仍然分配到 {format_trace(seg)}，说明阻塞后继续出现消息"
                )

                failed_child = make_failed_node(child, child_path, seg, reason)

                result = MatchResult(
                    matched=False,
                    blocked=False,
                    failed_nodes=[failed_child]
                )

                if verbose:
                    print(f"{prefix}离开节点 {node.label()}：{result.text()}")

                return result

            child_result = match(child, seg, indent + 2, child_path, verbose)

            if not child_result.matched:
                result = MatchResult(
                    matched=False,
                    blocked=False,
                    failed_nodes=child_result.failed_nodes
                )

                if verbose:
                    print(f"{prefix}    第 {idx} 个子结构失败，因此顺序结构失败")
                    print(f"{prefix}离开节点 {node.label()}：{result.text()}")

                return result

            if child_result.blocked:
                already_blocked = True

                if verbose:
                    print(f"{prefix}    第 {idx} 个子结构阻塞，后续结构不能再消费消息")

            else:
                if verbose:
                    print(f"{prefix}    第 {idx} 个子结构成功且未阻塞")

        result = MatchResult(matched=True, blocked=already_blocked)

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    # ------------------------------------------------------------
    # 8.5 排他/选择结构
    # ------------------------------------------------------------
    if node.op == "choice":
        if verbose:
            print(f"{prefix}  当前节点是排他/选择结构，只需一个分支能够匹配")

        trace_set = set(trace)
        candidates = []

        for child in node.children:
            if len(trace) == 0 or trace_set.issubset(child.messages):
                candidates.append(child)

        if verbose:
            print(f"{prefix}  根据消息集合进行选择分支初筛：")
            for idx, child in enumerate(node.children, start=1):
                print(
                    f"{prefix}    第 {idx} 个分支 path={path}/{idx}，"
                    f"{child.label()}，messages={sorted(child.messages)}，"
                    f"是否候选={child in candidates}"
                )

        all_candidate_failures: List[FailedNode] = []

        for idx, child in enumerate(node.children, start=1):
            if child not in candidates:
                continue

            child_path = f"{path}/{idx}"

            if verbose:
                print(f"{prefix}  尝试选择分支 {child.label()} 匹配 {format_trace(trace)}")

            child_result = match(child, trace, indent + 2, child_path, verbose)

            if child_result.matched:
                result = MatchResult(matched=True, blocked=child_result.blocked)

                if verbose:
                    print(f"{prefix}  选择结构采用分支 {child.label()}")
                    print(f"{prefix}离开节点 {node.label()}：{result.text()}")

                return result

            all_candidate_failures.extend(child_result.failed_nodes)

        if not candidates:
            reason = f"输入消息迹 {format_trace(trace)} 中的消息不属于任何选择分支的消息集合"

            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[make_failed_node(node, path, trace, reason)]
            )

        else:
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=all_candidate_failures
            )

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    # ------------------------------------------------------------
    # 8.6 并行结构
    # ------------------------------------------------------------
    if node.op == "par":
        if verbose:
            print(f"{prefix}  当前节点是并行结构，需要按分支消息集合投影")

        segments = split_for_parallel(node.children, trace)

        if segments is None:
            reason = f"输入消息迹 {format_trace(trace)} 中存在不属于任何并行分支的消息"

            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[make_failed_node(node, path, trace, reason)]
            )

            if verbose:
                print(f"{prefix}  {reason}")
                print(f"{prefix}离开节点 {node.label()}：{result.text()}")

            return result

        if verbose:
            print(f"{prefix}  并行结构划分结果：")
            for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
                print(
                    f"{prefix}    第 {idx} 个分支 path={path}/{idx}，"
                    f"{child.label()}，分配消息迹={format_trace(seg)}"
                )

        any_blocked = False
        failures: List[FailedNode] = []

        for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
            child_path = f"{path}/{idx}"
            child_result = match(child, seg, indent + 2, child_path, verbose)

            if not child_result.matched:
                failures.extend(child_result.failed_nodes)

            elif child_result.blocked:
                any_blocked = True

        if failures:
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=failures
            )

        else:
            result = MatchResult(
                matched=True,
                blocked=any_blocked
            )

        if verbose:
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")

        return result

    raise ValueError(f"未知节点类型: {node.op}")


# ============================================================
# 9. 修复分析：关系判断
# ============================================================

def first_last_positions(trace: List[str], messages: Set[str]) -> Tuple[Optional[int], Optional[int]]:
    """
    返回某个子树消息集合在 trace 中第一次、最后一次出现的位置。

    若完全没有出现，则返回 (None, None)。
    """
    positions = [i for i, m in enumerate(trace) if m in messages]

    if not positions:
        return None, None

    return min(positions), max(positions)


def relation_between_children(children: List[Node], traces: List[List[str]]) -> Dict[Tuple[int, int], str]:
    """
    判断每对子树之间的关系。

    返回值包括：

        exclusive:
            从不共现，倾向于排他。

        i_before_j:
            i 总是在 j 前面，倾向于顺序。

        j_before_i:
            j 总是在 i 前面，倾向于顺序，但需要重排。

        parallel:
            既观察到 i 在前，也观察到 j 在前，或存在交错，倾向于并行。

        unknown:
            暂时无法判断。
    """
    relations = {}

    for i in range(len(children)):
        for j in range(i + 1, len(children)):
            seen_cooccur = False
            i_before = False
            j_before = False
            overlap = False

            for tr in traces:
                fi, li = first_last_positions(tr, children[i].messages)
                fj, lj = first_last_positions(tr, children[j].messages)

                if fi is None or fj is None:
                    continue

                seen_cooccur = True

                if li < fj:
                    i_before = True
                elif lj < fi:
                    j_before = True
                else:
                    overlap = True

            if not seen_cooccur:
                rel = "exclusive"
            elif overlap:
                rel = "parallel"
            elif i_before and j_before:
                rel = "parallel"
            elif i_before:
                rel = "i_before_j"
            elif j_before:
                rel = "j_before_i"
            else:
                rel = "unknown"

            relations[(i, j)] = rel

    return relations


def infer_operator_from_relations(children: List[Node], traces: List[List[str]]) -> str:
    """
    根据子树在映射日志中的共现和顺序关系，推断父节点更适合的结构。

    返回：
        seq
        choice
        par
    """
    useful_children = [c for c in children if c.op != "tau"]

    if len(useful_children) <= 1:
        return "seq"

    appearing_counts = []

    for tr in traces:
        count = 0

        for c in useful_children:
            if project_trace(tr, c.messages):
                count += 1

        appearing_counts.append(count)

    # 每条 trace 中至多只出现一个子树，说明它们互斥。
    if appearing_counts and all(c <= 1 for c in appearing_counts):
        return "choice"

    relations = relation_between_children(useful_children, traces)
    rel_values = list(relations.values())

    # 如果任意 pair 出现双向顺序或交错，优先认为是并行。
    if any(r == "parallel" for r in rel_values):
        return "par"

    # 否则，如果都是稳定顺序或互斥关系，默认按顺序结构组织。
    if all(r in ["exclusive", "i_before_j", "j_before_i", "unknown"] for r in rel_values):
        return "seq"

    return "seq"


def order_children_by_log(children: List[Node], traces: List[List[str]]) -> List[Node]:
    """
    对顺序候选结构中的子树排序。

    简化做法：
        按照子树消息在日志中首次出现位置的平均值排序。
    """
    def avg_first_pos(child: Node) -> float:
        positions = []

        for tr in traces:
            f, _ = first_last_positions(tr, child.messages)

            if f is not None:
                positions.append(f)

        if not positions:
            return float("inf")

        return sum(positions) / len(positions)

    return sorted(children, key=avg_first_pos)


def message_op_map(node: Node) -> Dict[str, str]:
    """
    从原始子树中提取消息名到动作方向的映射。

    如果 A 在原模型中是 ?A，则修复时优先仍用 ?A；
    如果是 !A，则修复时优先仍用 !A。
    """
    out = {}

    if node.op in ["recv", "send"] and node.msg is not None:
        out[node.msg] = node.op

    for c in node.children:
        out.update(message_op_map(c))

    return out


def build_sequence_from_trace(trace: List[str], op_map: Dict[str, str]) -> Node:
    """
    根据日志片段构造顺序结构。
    """
    if not trace:
        return Node(op="tau")

    nodes = []

    for m in trace:
        op = op_map.get(m, "send")
        nodes.append(Node(op=op, msg=m))

    if len(nodes) == 1:
        return nodes[0]

    return Node(op="seq", children=nodes)


def build_choice_from_traces(traces: List[List[str]], op_map: Dict[str, str]) -> Node:
    """
    根据多条日志片段构造排他结构，每条不同片段作为一个分支。
    """
    unique = normalize_traces(traces)
    branches = [build_sequence_from_trace(t, op_map) for t in unique]

    if not branches:
        return Node(op="tau")

    if len(branches) == 1:
        return branches[0]

    return Node(op="choice", children=branches)


def all_traces_empty(traces: List[List[str]]) -> bool:
    return all(len(t) == 0 for t in traces)


def has_empty_and_nonempty(traces: List[List[str]]) -> bool:
    has_empty = any(len(t) == 0 for t in traces)
    has_nonempty = any(len(t) > 0 for t in traces)

    return has_empty and has_nonempty


# ============================================================
# 10. 递归修复算法
# ============================================================

def repair_leaf(node: Node, indent: int = 0) -> List[RepairCandidate]:
    """
    单节点修复规则：

    1. 如果映射日志全为空，则删除为 τ；
    2. 如果有时为空有时为原消息，则修复为 XOR(原节点, τ)；
    3. 如果映射日志是其他消息，则给出替换或新增选择分支；
    4. 如果基本一致，则保持不变。
    """
    prefix = "  " * indent
    traces = normalize_traces(node.mapped_traces)
    op_map = message_op_map(node)

    candidates = []

    print(f"{prefix}修复叶子节点 {node.label()}，映射日志={format_trace_set(traces)}")

    if all_traces_empty(traces):
        candidates.append(RepairCandidate(
            repaired_subtree=prepared(Node(op="tau")),
            cost=1,
            reason="该消息节点在映射日志中从未出现，候选修复为删除该节点，即替换为 τ"
        ))

    nonempty = [t for t in traces if len(t) > 0]
    unique_nonempty = normalize_traces(nonempty)

    if has_empty_and_nonempty(traces):
        candidates.append(RepairCandidate(
            repaired_subtree=prepared(Node(op="choice", children=[clone_node(node), Node(op="tau")])),
            cost=1,
            reason="该消息节点有时出现、有时缺失，候选修复为可选结构 XOR(节点, τ)"
        ))

    if unique_nonempty:
        observed_expr = prepared(build_choice_from_traces(unique_nonempty, op_map))

        same_as_original = (
            len(unique_nonempty) == 1
            and len(unique_nonempty[0]) == 1
            and unique_nonempty[0][0] == node.msg
        )

        if not same_as_original:
            candidates.append(RepairCandidate(
                repaired_subtree=observed_expr,
                cost=3,
                reason="映射日志中出现了与原消息节点不一致的消息，候选修复为用日志行为替换该节点"
            ))

            candidates.append(RepairCandidate(
                repaired_subtree=prepared(Node(op="choice", children=[clone_node(node), observed_expr])),
                cost=4,
                reason="映射日志中出现新行为，但保留原节点，候选修复为增加一个排他分支"
            ))

    if not candidates:
        candidates.append(RepairCandidate(
            repaired_subtree=prepared(clone_node(node)),
            cost=0,
            reason="叶子节点与映射日志基本一致，保持不变"
        ))

    return candidates


def repair_composite(node: Node, indent: int = 0) -> List[RepairCandidate]:
    """
    顺序、排他、并行结构的递归修复。

    核心思路：

    1. 先根据每个子节点的 mapped_traces 递归修复子节点；
    2. 再根据当前节点 mapped_traces 中各子树的关系判断父结构：
        - 互斥出现：choice
        - 稳定先后：seq
        - 多种交错：par
    3. 生成候选结构并设置修复代价；
    4. 由外层重新匹配验证候选是否有效。
    """
    prefix = "  " * indent
    traces = normalize_traces(node.mapped_traces)

    print(f"{prefix}修复复合节点 {node.label()}，映射日志={format_trace_set(traces)}")

    if all_traces_empty(traces):
        return [RepairCandidate(
            repaired_subtree=prepared(Node(op="tau")),
            cost=1,
            reason="该复合结构在映射日志中从未出现，候选修复为删除该结构，即替换为 τ"
        )]

    repaired_children = []
    child_cost_sum = 0
    child_reasons = []

    for idx, child in enumerate(node.children, start=1):
        print(f"{prefix}  递归修复第 {idx} 个子节点 {child.label()}")

        child_candidates = repair_subtree(child, indent + 2)
        best_child = min(child_candidates, key=lambda c: c.cost)

        repaired_children.append(prepared(best_child.repaired_subtree))
        child_cost_sum += best_child.cost
        child_reasons.append(f"子节点{idx}: {best_child.reason}")

    # 如果某些子树有时缺失，则给它增加 τ 分支，使其可选。
    optional_children = []

    for child, repaired_child in zip(node.children, repaired_children):
        if has_empty_and_nonempty(child.mapped_traces) and repaired_child.op != "tau":
            optional_children.append(
                prepared(Node(op="choice", children=[repaired_child, Node(op="tau")]))
            )
        else:
            optional_children.append(prepared(repaired_child))

    inferred_op = infer_operator_from_relations(optional_children, traces)

    candidates: List[RepairCandidate] = []

    # 候选 1：保持原父结构，只替换递归修复后的子树。
    keep = prepared(Node(op=node.op, children=optional_children))

    candidates.append(RepairCandidate(
        repaired_subtree=keep,
        cost=child_cost_sum,
        reason="保持原父结构，仅使用递归修复后的子树；" + "；".join(child_reasons)
    ))

    # 候选 2：按照日志关系修改父结构。
    if inferred_op == "seq":
        ordered = order_children_by_log(optional_children, traces)
        inferred = prepared(Node(op="seq", children=ordered))

        extra_cost = 0 if node.op == "seq" else 3

        if [node_to_expr(c) for c in ordered] != [node_to_expr(c) for c in optional_children]:
            extra_cost += 2

        candidates.append(RepairCandidate(
            repaired_subtree=inferred,
            cost=child_cost_sum + extra_cost,
            reason="根据映射日志中子树的稳定先后关系，将父结构修复/保持为顺序结构"
        ))

    elif inferred_op == "choice":
        observed_children = []

        for child, repaired_child in zip(node.children, optional_children):
            if any(project_trace(t, child.messages) for t in traces):
                observed_children.append(repaired_child)

        inferred = prepared(Node(op="choice", children=observed_children))

        extra_cost = 0 if node.op == "choice" else 3

        candidates.append(RepairCandidate(
            repaired_subtree=inferred,
            cost=child_cost_sum + extra_cost,
            reason="根据映射日志中子树互斥出现的关系，将父结构修复/保持为排他结构"
        ))

    elif inferred_op == "par":
        inferred = prepared(Node(op="par", children=optional_children))

        extra_cost = 0 if node.op == "par" else 3

        candidates.append(RepairCandidate(
            repaired_subtree=inferred,
            cost=child_cost_sum + extra_cost,
            reason="根据映射日志中子树的多种交错顺序，将父结构修复/保持为并行结构"
        ))

    # 候选 3：若当前节点存在无法映射到已有子节点的新消息，则增加新分支。
    new_parts = [u for u in node.unmapped_traces if len(u) > 0]

    if new_parts:
        op_map = message_op_map(node)
        new_branch = prepared(build_choice_from_traces(new_parts, op_map))

        if node.op == "choice":
            expanded = prepared(Node(op="choice", children=optional_children + [new_branch]))
            reason = "存在无法映射到任何已有分支的新消息，因此在排他结构中增加新分支"

        elif node.op == "par":
            expanded = prepared(Node(op="par", children=optional_children + [new_branch]))
            reason = "存在无法映射到任何已有并行分支的新消息，因此在并行结构中增加新分支"

        else:
            expanded = prepared(Node(op="seq", children=optional_children + [new_branch]))
            reason = "存在无法映射到任何已有顺序子结构的新消息，因此在顺序结构末尾增加新行为"

        candidates.append(RepairCandidate(
            repaired_subtree=expanded,
            cost=child_cost_sum + 2,
            reason=reason
        ))

    # 去重：相同表达式只保留代价更低的方案。
    dedup: Dict[str, RepairCandidate] = {}

    for c in candidates:
        expr = node_to_expr(c.repaired_subtree)

        if expr not in dedup or c.cost < dedup[expr].cost:
            dedup[expr] = c

    return list(dedup.values())


def repair_subtree(node: Node, indent: int = 0) -> List[RepairCandidate]:
    """
    对一个异常子树进行修复尝试。
    """
    if node.op in ["tau", "recv", "send"]:
        return repair_leaf(node, indent)

    return repair_composite(node, indent)


# ============================================================
# 11. 替换子树与重新匹配
# ============================================================

def find_node_by_path(node: Node, path: str) -> Node:
    """
    根据 path 定位表达式树中的节点。
    """
    if path == "root":
        return node

    parts = path.split("/")

    if parts[0] != "root":
        raise ValueError(f"非法路径: {path}")

    cur = node

    for p in parts[1:]:
        idx = int(p) - 1
        cur = cur.children[idx]

    return cur


def clone_with_replacement(node: Node, target_path: str, replacement: Node, path: str = "root") -> Node:
    """
    深拷贝整棵树，并把 target_path 对应的子树替换为 replacement。
    """
    if path == target_path:
        return clone_node(replacement)

    new_node = Node(op=node.op, msg=node.msg)

    new_children = []

    for idx, child in enumerate(node.children, start=1):
        child_path = f"{path}/{idx}"
        new_children.append(
            clone_with_replacement(child, target_path, replacement, child_path)
        )

    new_node.children = new_children

    return new_node


def ancestor_paths(path: str) -> List[str]:
    """
    返回某个失败节点的所有修复范围。

    例如：
        root/1/2 -> ['root/1/2', 'root/1', 'root']

    这样可以同时尝试：

        1. 只修复最底层失败节点；
        2. 修复其父结构；
        3. 必要时修复更高层结构。

    这一步很重要，因为有些异常是父结构关系错误，
    例如 !A!B 无法匹配 BA，真正需要修复的是 SEQ，而不是叶子 !A。
    """
    parts = path.split("/")
    scopes = []

    for end in range(len(parts), 0, -1):
        scopes.append("/".join(parts[:end]))

    return scopes


def try_repair_failed_nodes(
    root: Node,
    failed_nodes: List[FailedNode],
    trace: List[str]
) -> List[RepairAttempt]:
    """
    对所有匹配失败节点进行修复尝试。

    注意：
        最底层失败节点可以定位直接错误；
        但有些错误属于父结构关系错误，例如顺序反转、排他结构过窄、并行结构过宽。
        因此这里不仅尝试修复失败节点本身，也尝试修复它的祖先子树。
    """
    attempts = []

    if not failed_nodes:
        print("没有失败节点，因此不需要进行修复尝试。")
        return attempts

    print("\n[6] 开始对失败节点及其祖先结构进行修复尝试")
    print("-" * 80)

    tried_scopes = set()

    for fn in failed_nodes:
        print(f"\n失败节点 path={fn.path}, label={fn.label}")
        print(f"失败原因：{fn.reason}")

        scopes = ancestor_paths(fn.path)

        print("将依次尝试以下修复范围：", " -> ".join(scopes))

        for scope_path in scopes:
            if scope_path in tried_scopes:
                print(f"\n  修复范围 {scope_path} 已经尝试过，跳过。")
                continue

            tried_scopes.add(scope_path)

            target = find_node_by_path(root, scope_path)

            print(f"\n  当前修复范围：path={scope_path}, label={target.label()}")
            print(f"  原子树表达式：{node_to_expr(target)}")
            print(f"  子树映射日志：{format_trace_set(target.mapped_traces)}")

            candidates = repair_subtree(target, indent=2)
            candidates = sorted(candidates, key=lambda c: c.cost)

            for idx, cand in enumerate(candidates, start=1):
                print(f"\n    候选修复 {idx}:")
                print(f"      修复子树：{node_to_expr(cand.repaired_subtree)}")
                print(f"      修复代价：{cand.cost}")
                print(f"      修复原因：{cand.reason}")

                repaired_root = clone_with_replacement(root, scope_path, cand.repaired_subtree)
                repaired_root = simplify_node(repaired_root)
                compute_messages(repaired_root)
                reset_mapped_traces(repaired_root)

                print("      重新计算修复后模型的映射日志：")
                compute_mapped_traces_top_down(repaired_root, [trace], indent=4, path="root")

                rematch_result = match(repaired_root, trace, verbose=False)

                print(f"      修复后完整表达式：{node_to_expr(repaired_root)}")
                print(f"      重新匹配结果：{rematch_result.text()}")

                attempts.append(RepairAttempt(
                    failed_path=scope_path,
                    failed_label=target.label(),
                    original_subtree_expr=node_to_expr(target),
                    repaired_subtree_expr=node_to_expr(cand.repaired_subtree),
                    repaired_whole_expr=node_to_expr(repaired_root),
                    cost=cand.cost,
                    reason=cand.reason,
                    rematch_result=rematch_result
                ))

    return attempts


def print_repair_summary(attempts: List[RepairAttempt]) -> None:
    """
    打印所有修复尝试的汇总结果。
    """
    print("\n[7] 修复尝试汇总")
    print("=" * 80)

    if not attempts:
        print("没有生成修复尝试。")
        return

    successful = [a for a in attempts if a.rematch_result.matched]
    failed = [a for a in attempts if not a.rematch_result.matched]

    print(f"共生成候选修复方案 {len(attempts)} 个。")
    print(f"其中重新匹配成功 {len(successful)} 个，仍失败 {len(failed)} 个。")

    if successful:
        print("\n可使模型重新匹配的候选方案：")

        for i, a in enumerate(sorted(successful, key=lambda x: x.cost), start=1):
            print(f"  [{i}] 修复范围 path={a.failed_path}, label={a.failed_label}")
            print(f"      原子树：{a.original_subtree_expr}")
            print(f"      修复子树：{a.repaired_subtree_expr}")
            print(f"      修复后完整表达式：{a.repaired_whole_expr}")
            print(f"      代价：{a.cost}")
            print(f"      原因：{a.reason}")
            print(f"      重新匹配：{a.rematch_result.text()}")

    if failed:
        print("\n仍无法重新匹配的候选方案：")

        for i, a in enumerate(failed, start=1):
            print(f"  [{i}] path={a.failed_path}, label={a.failed_label}, 修复子树={a.repaired_subtree_expr}")
            print(f"      修复后表达式：{a.repaired_whole_expr}")
            print(f"      重新匹配：{a.rematch_result.text()}")


# ============================================================
# 12. 主函数
# ============================================================

def match_and_repair_message_pattern(expr: str, trace_str: str) -> Tuple[MatchResult, List[RepairAttempt]]:
    """
    完整流程：

    1. 词法分析；
    2. 解析为表达式树；
    3. 自底向上计算 messages；
    4. 自顶向下计算每个子树的 mapped_traces；
    5. 执行原有匹配算法；
    6. 若失败，定位更靠近树底的失败节点；
    7. 对失败节点及其祖先结构进行递归修复尝试；
    8. 对每个候选修复结果重新匹配验证。
    """
    print("=" * 80)
    print("输入消息模式表达式：", expr)
    print("输入消息迹：", trace_str)
    print("=" * 80)

    tokens = tokenize(expr)

    print("\n[1] 词法分析结果：")
    print(tokens)

    parser = Parser(tokens)
    root = parser.parse()

    print("\n[2] 初始表达式树及每个节点的消息集合：")
    print_tree(root)

    trace = trace_to_list(trace_str)

    print("\n[3] 自顶向下计算每个子树的映射日志序列：")
    reset_mapped_traces(root)
    compute_mapped_traces_top_down(root, [trace], indent=0, path="root")

    print("\n[4] 带映射日志的表达式树：")
    print_tree(root)

    print("\n[5] 开始匹配过程：")
    result = match(root, trace, indent=0, path="root", verbose=True)

    print("\n[5.1] 匹配最终结果：")
    print("匹配状态：", result.text())
    print("是否属于 MP(E)：", result.matched)
    print("是否阻塞：", result.blocked)

    print("\n[5.2] 更靠近树底的失败匹配节点：")
    print_failed_nodes(result.failed_nodes)

    attempts = try_repair_failed_nodes(root, result.failed_nodes, trace)

    print_repair_summary(attempts)

    return result, attempts


def match_message_pattern(expr: str, trace_str: str) -> MatchResult:
    """
    为了兼容之前的函数名，保留一个简单包装。
    """
    result, _ = match_and_repair_message_pattern(expr, trace_str)
    return result


# ============================================================
# 13. 示例运行
# ============================================================

if __name__ == "__main__":
    # 示例 1：
    # 原表达式：?A!B(?C||!D+!E)!F
    # 消息迹：ABDE
    # 这个 trace 往往会触发失败定位和修复尝试。
    expr1 = "?A!B(?C||!D+!E)!F"
    trace1 = "ABDE"

    match_and_repair_message_pattern(expr1, trace1)

    print("\n" + "#" * 80 + "\n")

    # 示例 2：
    # 顺序反转示例。
    # 原模型要求 A 后 B，但日志是 BA。
    # 递归修复会尝试把 SEQ(!A,!B) 修复为 SEQ(!B,!A)。
    expr2 = "!A!B"
    trace2 = "BA"

    match_and_repair_message_pattern(expr2, trace2)

    print("\n" + "#" * 80 + "\n")

    # 示例 3：
    # 排他结构中同时出现两个分支。
    # 原模型 !A+!B 不允许 AB 同时出现，
    # 修复时会尝试在父结构层面调整。
    expr3 = "!A+!B"
    trace3 = "AB"

    match_and_repair_message_pattern(expr3, trace3)