from __future__ import annotations

from pathlib import Path
import unittest

from chapter7.diagnosis import RepairScope
from chapter7.model import Node, ProcessModel
from chapter7.repairs import generate_repair_candidates


def model_for(tree: Node, sends=(), receives=()) -> ProcessModel:
    return ProcessModel(
        process_id="p",
        participant_name="participant",
        tree=tree,
        sends=frozenset(sends),
        receives=frozenset(receives),
        source_bpmn=Path("source.bpmn"),
        process_bpmn=Path("process.bpmn"),
    )


class RepairTests(unittest.TestCase):
    def test_rule_2_optional_message_is_behavior_extending(self) -> None:
        tree = Node.leaf("A")
        model = model_for(tree, sends=("A",))
        scope = RepairScope((), tree, "minimal", ((),), ((),), (2,))
        candidates = generate_repair_candidates(
            model,
            scope,
            normal_traces=(("A",),),
            abnormal_traces=((),),
        )
        optional = next(candidate for candidate in candidates if candidate.rule_id == 2)
        self.assertEqual(optional.after_expression, "+(A,tau)")
        self.assertTrue(optional.definition_717_satisfied)
        self.assertEqual(optional.normal_after_pass, 1)
        self.assertEqual(optional.abnormal_after_pass, 1)

    def test_rule_5_sequence_to_parallel_preserves_and_extends_behavior(self) -> None:
        tree = Node.composite("seq", (Node.leaf("A"), Node.leaf("B")))
        model = model_for(tree, sends=("A", "B"))
        scope = RepairScope((), tree, "minimal", (("A", "B"), ("B", "A")), ((),), (1, 2))
        candidates = generate_repair_candidates(
            model,
            scope,
            normal_traces=(("A", "B"),),
            abnormal_traces=(("B", "A"),),
        )
        rule5 = next(candidate for candidate in candidates if candidate.rule_id == 5)
        self.assertEqual(rule5.after_expression, "|(A,B)")
        self.assertTrue(rule5.definition_717_satisfied)
        self.assertEqual(rule5.normal_after_pass, 1)
        self.assertEqual(rule5.abnormal_after_pass, 1)

    def test_rule_12_parallel_to_choice_for_paper_sibling_fragments(self) -> None:
        tree = Node.composite("parallel", (Node.leaf("M7"), Node.leaf("M5")))
        model = model_for(tree, receives=("M7", "M5"))
        scope = RepairScope((), tree, "merged", (("M7",), ("M5",)), ((0,), (1,)), (1, 2))
        candidates = generate_repair_candidates(
            model,
            scope,
            normal_traces=(("M7", "M5"),),
            abnormal_traces=(("M7",), ("M5",)),
        )
        self.assertEqual({candidate.rule_id for candidate in candidates}, {12})
        self.assertEqual(candidates[0].after_expression, "+(M7,M5)")
        self.assertEqual(candidates[0].scope_observations_covered, 2)


if __name__ == "__main__":
    unittest.main()
