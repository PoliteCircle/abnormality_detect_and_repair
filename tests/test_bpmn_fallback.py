from __future__ import annotations

import unittest

from analysis.bpmn import UnsupportedModelError, _ReducedTreeParser
from analysis.model import node_to_expression


class ReducedTreeFallbackTests(unittest.TestCase):
    def test_deep_seq_parallel_choice_expression(self) -> None:
        parser = _ReducedTreeParser(
            "->('Task_A',+('Task_B','Task_C'),X('Task_D',tau),'Internal')",
            {
                "Task_A": "M1",
                "Task_B": "M2",
                "Task_C": "M3",
                "Task_D": "M4",
            },
        )
        self.assertEqual(
            node_to_expression(parser.parse()),
            ".(M1,|(M2,M3),+(M4,tau))",
        )

    def test_single_bare_activity_label(self) -> None:
        parser = _ReducedTreeParser("Task_Only", {"Task_Only": "M1"})
        self.assertEqual(node_to_expression(parser.parse()), "M1")

    def test_loop_is_rejected(self) -> None:
        parser = _ReducedTreeParser("*('Task_A',tau)", {"Task_A": "M1"})
        with self.assertRaises(UnsupportedModelError):
            parser.parse()


if __name__ == "__main__":
    unittest.main()

