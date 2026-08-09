from dataclasses import dataclass, field
from typing import List, Set, Optional


# ============================================================
# 1. 表达式树节点定义
# ============================================================

@dataclass
class Node:
    """
    消息模式表达式树节点。

    op:
        recv    : 接收消息，如 ?A
        send    : 发送消息，如 !B
        seq     : 顺序结构，如 XY
        choice  : 选择结构，如 X+Y
        par     : 并行结构，如 X||Y

    msg:
        叶子节点对应的消息名，例如 ?A 和 !A 的 msg 都是 A。

    children:
        非叶子节点的子节点列表。

    messages:
        当前子树中包含的所有消息名。
    """
    op: str
    msg: Optional[str] = None
    children: List["Node"] = field(default_factory=list)
    messages: Set[str] = field(default_factory=set)

    def label(self) -> str:
        if self.op == "recv":
            return f"?{self.msg}"
        if self.op == "send":
            return f"!{self.msg}"
        if self.op == "seq":
            return "SEQ"
        if self.op == "choice":
            return "CHOICE(+)"
        if self.op == "par":
            return "PAR(||)"
        return self.op


@dataclass
class FailedNode:
    """
    失败匹配节点。

    只记录最终返回的、尽量靠近树底的失败节点。
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

    matched:
        是否匹配成功。

    blocked:
        是否处于阻塞状态。
        blocked=True 表示消息迹是合法阻塞前缀。
        blocked=False 表示消息迹开放完成。

    failed_nodes:
        未能成功匹配的子树节点。
        本版本只返回更靠近树底的失败节点。
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
    美化输出消息迹。
    """
    if not trace:
        return "ε"
    return "".join(trace)


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

    例如：
        ?A!B(?C||!D+!E)!F

    会被拆成：
        ['?A', '!B', '(', '?C', '||', '!D', '+', '!E', ')', '!F']
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
    解析优先级约定：

        1. 括号最高
        2. 顺序连接次之
        3. 并行 || 再次
        4. 选择 + 最低

    因此：
        ?C||!D+!E

    会被解析为：
        (?C || !D) + !E
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

        if tok is not None and tok.startswith("?"):
            self.consume()
            return Node(op="recv", msg=tok[1:])

        if tok is not None and tok.startswith("!"):
            self.consume()
            return Node(op="send", msg=tok[1:])

        raise ValueError(f"无法解析 factor: {tok}")


# ============================================================
# 5. 为每个树节点计算消息集合
# ============================================================

def compute_messages(node: Node) -> Set[str]:
    """
    自底向上计算每个节点的消息集合。
    """
    if node.op in ["recv", "send"]:
        node.messages = {node.msg}
    else:
        msg_set = set()
        for child in node.children:
            msg_set |= compute_messages(child)
        node.messages = msg_set

    return node.messages


def print_tree(node: Node, indent: int = 0, path: str = "root") -> None:
    """
    打印表达式树结构，以及每个节点的消息集合。
    """
    prefix = "  " * indent
    print(f"{prefix}- path={path}, {node.label()}, messages={sorted(node.messages)}")

    for idx, child in enumerate(node.children, start=1):
        print_tree(child, indent + 1, f"{path}/{idx}")


# ============================================================
# 6. 根据消息集合进行初步分配
# ============================================================

def split_for_sequence(children: List[Node], trace: List[str]) -> List[List[str]]:
    """
    顺序结构的初步划分。

    对顺序结构的每个子树，根据该子树的 messages 集合，
    从左到右尽可能消费属于该子树的消息。

    如果最后还有无法被后续结构消费的消息，则放入最后一个段，
    让后续匹配逻辑给出失败定位。
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
    并行结构的划分。

    根据并行结构每个子树的 messages 集合，
    把输入消息迹投影到对应子树。

    注意：
        这里假设并行结构的不同分支消息名不重叠。
        如果一个消息同时属于多个分支，会产生歧义，代码会抛出错误。
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
# 7. 核心匹配算法：只返回更靠近树底的失败节点
# ============================================================

def match(node: Node, trace: List[str], indent: int = 0, path: str = "root") -> MatchResult:
    """
    递归匹配消息迹 trace 是否属于当前子表达式 node 的消息模式。

    返回：
        MatchResult(matched=True, blocked=False)
            表示匹配成功，并且开放完成。

        MatchResult(matched=True, blocked=True)
            表示匹配成功，但是已经阻塞。

        MatchResult(matched=False, blocked=False, failed_nodes=[...])
            表示匹配失败，并返回尽量靠近树底的失败节点。
    """
    prefix = "  " * indent

    print(
        f"{prefix}进入节点 {node.label()}，"
        f"path={path}，"
        f"节点消息集合={sorted(node.messages)}，"
        f"待匹配消息迹={format_trace(trace)}"
    )

    # ------------------------------------------------------------
    # 7.1 接收消息 ?m
    # ------------------------------------------------------------
    if node.op == "recv":
        if len(trace) == 0:
            print(f"{prefix}  接收节点 ?{node.msg} 匹配 ε：消息未到达，形成合法阻塞前缀")
            result = MatchResult(matched=True, blocked=True)
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        if len(trace) == 1 and trace[0] == node.msg:
            print(f"{prefix}  接收节点 ?{node.msg} 匹配 {trace[0]}：成功接收，未阻塞")
            result = MatchResult(matched=True, blocked=False)
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        reason = (
            f"接收节点 ?{node.msg} 只能匹配 ε 或 {node.msg}，"
            f"但实际分配到 {format_trace(trace)}"
        )
        print(f"{prefix}  {reason}")

        failed = make_failed_node(node, path, trace, reason)
        result = MatchResult(
            matched=False,
            blocked=False,
            failed_nodes=[failed]
        )
        print(f"{prefix}离开节点 {node.label()}：{result.text()}")
        return result

    # ------------------------------------------------------------
    # 7.2 发送消息 !m
    # ------------------------------------------------------------
    if node.op == "send":
        if len(trace) == 1 and trace[0] == node.msg:
            print(f"{prefix}  发送节点 !{node.msg} 匹配 {trace[0]}：成功发送，未阻塞")
            result = MatchResult(matched=True, blocked=False)
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        if len(trace) == 0:
            reason = f"发送节点 !{node.msg} 不能匹配 ε，发送动作缺失"
        else:
            reason = (
                f"发送节点 !{node.msg} 只能匹配 {node.msg}，"
                f"但实际分配到 {format_trace(trace)}"
            )

        print(f"{prefix}  {reason}")

        failed = make_failed_node(node, path, trace, reason)
        result = MatchResult(
            matched=False,
            blocked=False,
            failed_nodes=[failed]
        )
        print(f"{prefix}离开节点 {node.label()}：{result.text()}")
        return result

    # ------------------------------------------------------------
    # 7.3 顺序结构 XY
    # ------------------------------------------------------------
    if node.op == "seq":
        print(f"{prefix}  当前节点是顺序结构，需要从左到右依次匹配各个子结构")

        segments = split_for_sequence(node.children, trace)

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
            print(f"{prefix}  开始匹配第 {idx} 个顺序子结构：{child.label()}")

            # 如果前面的结构已经阻塞，则后续结构不能再消费消息。
            if already_blocked:
                if len(seg) == 0:
                    print(
                        f"{prefix}    前序结构已经阻塞，"
                        f"当前子结构 {child.label()} 分配到 ε，保持阻塞状态"
                    )
                    continue

                reason = (
                    f"前序结构已经阻塞，但当前子结构 {child.label()} "
                    f"仍然分配到 {format_trace(seg)}，说明阻塞后继续出现消息"
                )
                print(f"{prefix}    {reason}")

                # 只返回更靠近树底的当前子节点，不返回外层 SEQ。
                failed_child = make_failed_node(child, child_path, seg, reason)
                result = MatchResult(
                    matched=False,
                    blocked=False,
                    failed_nodes=[failed_child]
                )
                print(f"{prefix}离开节点 {node.label()}：{result.text()}")
                return result

            child_result = match(child, seg, indent + 2, child_path)

            if not child_result.matched:
                print(
                    f"{prefix}    第 {idx} 个子结构 {child.label()} 匹配失败，"
                    f"因此顺序结构匹配失败"
                )

                # 子节点已经给出了更靠近树底的失败节点，
                # 外层 SEQ 不再加入失败列表。
                result = MatchResult(
                    matched=False,
                    blocked=False,
                    failed_nodes=child_result.failed_nodes
                )
                print(f"{prefix}离开节点 {node.label()}：{result.text()}")
                return result

            if child_result.blocked:
                print(
                    f"{prefix}    第 {idx} 个子结构 {child.label()} 匹配成功但已经阻塞，"
                    f"因此整个顺序结构进入阻塞状态"
                )
                already_blocked = True
            else:
                print(
                    f"{prefix}    第 {idx} 个子结构 {child.label()} 匹配成功且未阻塞，"
                    f"继续匹配后续顺序结构"
                )

        result = MatchResult(matched=True, blocked=already_blocked)
        print(f"{prefix}离开节点 {node.label()}：{result.text()}")
        return result

    # ------------------------------------------------------------
    # 7.4 选择结构 X + Y
    # ------------------------------------------------------------
    if node.op == "choice":
        print(f"{prefix}  当前节点是选择结构，只需选择一个能够匹配的分支")

        trace_set = set(trace)

        candidates = []
        for child in node.children:
            if len(trace) == 0 or trace_set.issubset(child.messages):
                candidates.append(child)

        print(f"{prefix}  根据消息集合进行选择分支初筛：")
        for idx, child in enumerate(node.children, start=1):
            possible = child in candidates
            print(
                f"{prefix}    第 {idx} 个分支 path={path}/{idx}，"
                f"{child.label()}，"
                f"消息集合={sorted(child.messages)}，"
                f"是否可能匹配={possible}"
            )

        all_candidate_failures: List[FailedNode] = []

        for idx, child in enumerate(node.children, start=1):
            if child not in candidates:
                continue

            child_path = f"{path}/{idx}"
            print(f"{prefix}  尝试选择分支 {child.label()} 匹配 {format_trace(trace)}")

            child_result = match(child, trace, indent + 2, child_path)

            if child_result.matched:
                print(
                    f"{prefix}  选择结构采用分支 {child.label()}，"
                    f"该分支结果为：{child_result.text()}"
                )

                # 只要某个分支匹配成功，选择结构就是成功。
                # 其他候选分支失败不作为最终失败节点。
                result = MatchResult(
                    matched=True,
                    blocked=child_result.blocked
                )
                print(f"{prefix}离开节点 {node.label()}：{result.text()}")
                return result

            print(f"{prefix}  分支 {child.label()} 匹配失败，尝试其他分支")
            all_candidate_failures.extend(child_result.failed_nodes)

        if not candidates:
            reason = (
                f"输入消息迹 {format_trace(trace)} 中的消息不属于任何选择分支的消息集合"
            )
            print(f"{prefix}  {reason}")

            # 没有候选分支时，无法定位到更深子节点，
            # 因此当前 CHOICE 是最具体的可诊断失败节点。
            failed_choice = make_failed_node(node, path, trace, reason)
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[failed_choice]
            )
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        print(f"{prefix}  所有候选选择分支均匹配失败")

        # 只返回候选分支中更靠近树底的失败节点，不返回当前 CHOICE。
        result = MatchResult(
            matched=False,
            blocked=False,
            failed_nodes=all_candidate_failures
        )
        print(f"{prefix}离开节点 {node.label()}：{result.text()}")
        return result

    # ------------------------------------------------------------
    # 7.5 并行结构 X || Y
    # ------------------------------------------------------------
    if node.op == "par":
        print(f"{prefix}  当前节点是并行结构，需要按照各分支消息集合对消息迹进行投影划分")

        segments = split_for_parallel(node.children, trace)

        if segments is None:
            reason = (
                f"输入消息迹 {format_trace(trace)} 中存在不属于任何并行分支的消息"
            )
            print(f"{prefix}  {reason}")

            # 无法把未知消息定位到某个具体分支，
            # 因此当前 PAR 是最具体的可诊断失败节点。
            failed_par = make_failed_node(node, path, trace, reason)
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=[failed_par]
            )
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        print(f"{prefix}  并行结构划分结果：")
        for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
            print(
                f"{prefix}    第 {idx} 个分支 path={path}/{idx}，"
                f"{child.label()}，"
                f"消息集合={sorted(child.messages)}，"
                f"分配消息迹={format_trace(seg)}"
            )

        any_blocked = False
        all_branch_failures: List[FailedNode] = []

        for idx, (child, seg) in enumerate(zip(node.children, segments), start=1):
            child_path = f"{path}/{idx}"
            print(f"{prefix}  开始匹配第 {idx} 个并行分支：{child.label()}")

            child_result = match(child, seg, indent + 2, child_path)

            if not child_result.matched:
                print(
                    f"{prefix}    并行分支 {child.label()} 匹配失败，"
                    f"因此整个并行结构匹配失败"
                )

                # 只返回失败分支中的底层失败节点，不返回当前 PAR。
                all_branch_failures.extend(child_result.failed_nodes)

            elif child_result.blocked:
                print(
                    f"{prefix}    并行分支 {child.label()} 匹配成功但阻塞，"
                    f"因此整个并行结构最终会处于阻塞状态"
                )
                any_blocked = True
            else:
                print(f"{prefix}    并行分支 {child.label()} 匹配成功且未阻塞")

        if all_branch_failures:
            result = MatchResult(
                matched=False,
                blocked=False,
                failed_nodes=all_branch_failures
            )
            print(f"{prefix}离开节点 {node.label()}：{result.text()}")
            return result

        result = MatchResult(matched=True, blocked=any_blocked)

        if any_blocked:
            print(f"{prefix}  并行结构中至少一个分支阻塞，因此并行结构阻塞")
        else:
            print(f"{prefix}  并行结构所有分支均开放完成，因此并行结构未阻塞")

        print(f"{prefix}离开节点 {node.label()}：{result.text()}")
        return result

    raise ValueError(f"未知节点类型: {node.op}")


# ============================================================
# 8. 主函数
# ============================================================

def match_message_pattern(expr: str, trace_str: str) -> MatchResult:
    print("=" * 80)
    print("输入消息模式表达式：", expr)
    print("输入消息迹：", trace_str)
    print("=" * 80)

    tokens = tokenize(expr)
    print("\n[1] 词法分析结果：")
    print(tokens)

    parser = Parser(tokens)
    root = parser.parse()

    print("\n[2] 表达式树及每个节点的消息集合：")
    print_tree(root)

    trace = trace_to_list(trace_str)

    print("\n[3] 开始匹配过程：")
    result = match(root, trace, indent=0, path="root")

    print("\n[4] 最终结果：")
    print("匹配状态：", result.text())
    print("是否属于 MP(E)：", result.matched)
    print("是否阻塞：", result.blocked)

    print("\n[5] 更靠近树底的失败匹配节点：")
    print_failed_nodes(result.failed_nodes)

    return result


# ============================================================
# 9. 示例运行
# ============================================================

if __name__ == "__main__":
    # 示例 1：合法阻塞前缀
    expr1 = "?A!B(?C||!D+!E)!F"
    trace1 = "ABDE"

    result1 = match_message_pattern(expr1, trace1)

    print("\n程序返回：")
    print("matched =", result1.matched)
    print("blocked =", result1.blocked)
    print("failed_nodes =", result1.failed_nodes)

    print("\n" + "#" * 80 + "\n")