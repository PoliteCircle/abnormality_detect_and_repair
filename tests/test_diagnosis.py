from __future__ import annotations

import unittest

from analysis.diagnosis import build_repair_scopes, find_minimal_abnormal_structures
from analysis.model import Node, node_to_expression


class MinimalAbnormalStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port_tree = Node.composite(
            "seq",
            (
                Node.composite("parallel", (Node.leaf("M4"), Node.leaf("M6"))),
                Node.composite("parallel", (Node.leaf("M7"), Node.leaf("M5"))),
                Node.leaf("M8"),
            ),
        )

    def diagnose(self, trace, index=1):
        return find_minimal_abnormal_structures(
            self.port_tree,
            trace,
            source_index=index,
            line_number=index,
            global_trace=trace,
        )

    def test_missing_parallel_branch_returns_leaf_m5(self) -> None:
        result = self.diagnose(("M4", "M6", "M7", "M8"))
        self.assertEqual(
            [node_to_expression(item.node) for item in result.minimal_structures],
            ["M5"],
        )
        self.assertEqual(result.minimal_structures[0].assigned_trace, ())

    def test_merges_sibling_minas(self) -> None:
        first = self.diagnose(("M4", "M6", "M7", "M8"), 1)
        second = self.diagnose(("M4", "M6", "M5", "M8"), 2)
        records = first.minimal_structures + second.minimal_structures
        scopes = build_repair_scopes(self.port_tree, records)
        merged = [scope for scope in scopes if scope.kind == "merged"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(node_to_expression(merged[0].node), "|(M7,M5)")
        self.assertEqual(set(merged[0].observations), {("M7",), ("M5",)})

    def test_reversed_sequence_is_the_abnormal_structure(self) -> None:
        tree = Node.composite("seq", (Node.leaf("A"), Node.leaf("B")))
        result = find_minimal_abnormal_structures(
            tree,
            ("B", "A"),
            source_index=1,
            line_number=1,
            global_trace=("B", "A"),
        )
        self.assertEqual(len(result.minimal_structures), 1)
        self.assertEqual(node_to_expression(result.minimal_structures[0].node), ".(A,B)")

    def test_flat_nary_sequence_is_reduced_to_the_inverted_pair(self) -> None:
        tree = Node.composite(
            "seq",
            tuple(Node.leaf(name) for name in ("M1", "M2", "M3", "M4")),
        )
        result = find_minimal_abnormal_structures(
            tree,
            ("M1", "M3", "M2", "M4"),
            source_index=1,
            line_number=1,
            global_trace=("M1", "M3", "M2", "M4"),
        )
        item = result.minimal_structures[0]
        self.assertEqual(node_to_expression(item.node), ".(M2,M3)")
        self.assertEqual(item.child_slice, (1, 2))
        self.assertEqual(item.assigned_trace, ("M3", "M2"))


if __name__ == "__main__":
    unittest.main()
