from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple, Union

# ============================================================
# AST 定义（论文表达式）
#  Leaf("M1") ：叶子节点（这里我们最终会让叶子是消息名 M1/M2...）
#  OpNode(".", (...)) ：顺序结构
#  OpNode("+", (...)) ：选择结构
#  OpNode("|", (...)) ：并行结构
# ============================================================

@dataclass(frozen=True)
class Leaf:
    name: str

@dataclass(frozen=True)
class OpNode:
    op: str  # '.', '+', '|'
    children: Tuple["Expr", ...]

Expr = Union[Leaf, OpNode]


# ============================================================
# Parser：解析形如 .(A,B,|(C,+(D,E))) 的表达式为 AST
# 说明：
#   - 这里只支持 name token = [0-9A-Za-z_]+
#   - 所以我们会对 BPMN 的 name 做 normalize
# ============================================================

class Parser:
    def __init__(self, s: str):
        self.s = s.replace(" ", "")
        self.n = len(self.s)
        self.i = 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def eat(self, ch: str) -> None:
        if self.peek() != ch:
            raise ValueError(f"Expect '{ch}' at pos {self.i}, got '{self.peek()}' in {self.s}")
        self.i += 1

    def parse(self) -> Expr:
        e = self.parse_expr()
        if self.i != self.n:
            raise ValueError(f"Unconsumed tail at pos {self.i}: {self.s[self.i:]}")
        return e

    def parse_expr(self) -> Expr:
        c = self.peek()
        if c in ".+|":
            op = c
            self.i += 1
            self.eat("(")
            kids = [self.parse_expr()]
            while self.peek() == ",":
                self.i += 1
                kids.append(self.parse_expr())
            self.eat(")")
            return OpNode(op=op, children=tuple(kids))

        name = self.parse_name()
        return Leaf(name=name)

    def parse_name(self) -> str:
        if not self.peek():
            raise ValueError("Unexpected end while parsing name")
        start = self.i
        while self.peek() and (self.peek().isalnum() or self.peek() == "_"):
            self.i += 1
        if start == self.i:
            raise ValueError(f"Invalid name at pos {self.i} in {self.s}")
        return self.s[start:self.i]