from __future__ import annotations

import unittest

from chapter7.model import Node
from chapter7.patterns import accepts_trace, enumerate_patterns


class MessagePatternTests(unittest.TestCase):
    def test_paper_agency_pattern(self) -> None:
        tree = Node.composite(
            "seq",
            tuple(Node.leaf(name) for name in ("M1", "M2", "M5", "M3", "M6", "M7", "M8")),
        )
        sends = frozenset(("M2", "M5", "M3", "M6", "M7"))
        receives = frozenset(("M1", "M8"))
        patterns = enumerate_patterns(tree, sends, receives)
        self.assertEqual(
            patterns.all,
            {
                (),
                ("M1", "M2", "M5", "M3", "M6", "M7"),
                ("M1", "M2", "M5", "M3", "M6", "M7", "M8"),
            },
        )

    def test_paper_port_pattern_has_thirteen_traces(self) -> None:
        tree = Node.composite(
            "seq",
            (
                Node.composite("parallel", (Node.leaf("M4"), Node.leaf("M6"))),
                Node.composite("parallel", (Node.leaf("M7"), Node.leaf("M5"))),
                Node.leaf("M8"),
            ),
        )
        patterns = enumerate_patterns(
            tree,
            frozenset(("M8",)),
            frozenset(("M4", "M5", "M6", "M7")),
        )
        self.assertFalse(patterns.truncated)
        self.assertEqual(len(patterns.all), 13)
        self.assertIn(("M4", "M6", "M7", "M5", "M8"), patterns.open)
        self.assertIn(("M6", "M4", "M5"), patterns.closed)

    def test_exact_membership_does_not_depend_on_display_limit(self) -> None:
        names = tuple(f"M{index}" for index in range(8))
        tree = Node.composite("parallel", tuple(Node.leaf(name) for name in names))
        patterns = enumerate_patterns(tree, frozenset(names), frozenset(), limit=10)
        self.assertTrue(patterns.truncated)
        self.assertTrue(accepts_trace(tree, tuple(reversed(names)), frozenset(names), frozenset()))


if __name__ == "__main__":
    unittest.main()
