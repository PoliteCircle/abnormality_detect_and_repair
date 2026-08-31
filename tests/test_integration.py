from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from analysis.pipeline import run_analysis


class RepositoryIntegrationTests(unittest.TestCase):
    def test_quote_order_choice_anomaly_end_to_end(self) -> None:
        root = Path(__file__).resolve().parents[1]
        case = root / "experiments" / "quote_order"
        with tempfile.TemporaryDirectory() as directory:
            report = run_analysis(
                case / "collaboration.bpmn",
                case / "global_log_2.txt",
                Path(directory),
                pattern_limit=500,
                behavior_limit=2_000,
                write_json=True,
            )
            self.assertIsNotNone(report.json_path)
            assert report.json_path is not None
            with report.json_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            self.assertEqual(payload["summary"]["violating_process_count"], 2)
        self.assertEqual(len(report.processes), 2)
        self.assertEqual(report.violating_process_count, 2)
        for process in report.processes:
            rules = {
                candidate.rule_id
                for scope in process.scopes
                for candidate in scope.candidates
            }
            self.assertIn(8, rules)

    def test_port_order_anomaly_prefers_parallel_repair(self) -> None:
        root = Path(__file__).resolve().parents[1]
        case = root / "experiments" / "qingdao_port_simple"
        with tempfile.TemporaryDirectory() as directory:
            report = run_analysis(
                case / "collaboration.bpmn",
                case / "global_log_1.txt",
                Path(directory),
                pattern_limit=500,
                behavior_limit=5_000,
                write_json=False,
            )
        affected = [process for process in report.processes if process.violating]
        self.assertEqual({process.model.participant_name for process in affected}, {"forwarder", "agency"})
        for process in affected:
            best = next(
                candidate
                for scope in process.scopes
                for candidate in scope.candidates
                if candidate.rule_id == 5
            )
            self.assertEqual(best.before_expression, ".(M2,M3)")
            self.assertEqual(best.after_expression, "|(M2,M3)")
            self.assertTrue(best.behavior_satisfied)
            self.assertEqual(best.normal_after_pass, best.normal_total)
            self.assertEqual(best.abnormal_after_pass, best.abnormal_total)


if __name__ == "__main__":
    unittest.main()
