from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExtendedBenchmarkManifestTests(unittest.TestCase):
    def test_default_manifest_is_complete_and_hashes_match(self) -> None:
        manifest_path = ROOT / "experiments" / "extended_benchmark_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["entries"]
        self.assertEqual(manifest["seed"], 20260809)
        self.assertEqual(manifest["entry_count"], 154)
        self.assertEqual(len(entries), 154)
        self.assertEqual(len({entry["case"] for entry in entries}), 14)
        self.assertTrue(all(entry["detected"] for entry in entries))
        self.assertTrue(all(entry["strict_repair_success"] for entry in entries))
        for entry in entries:
            bpmn_path = ROOT / entry["bpmn_file"]
            log_path = ROOT / entry["log_file"]
            self.assertEqual(_sha256(bpmn_path), entry["bpmn_sha256"])
            self.assertEqual(_sha256(log_path), entry["log_sha256"])

    def test_synthetic_anomaly_types_are_balanced(self) -> None:
        manifest = json.loads(
            (ROOT / "experiments" / "extended_benchmark_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        synthetic = [
            entry
            for entry in manifest["entries"]
            if entry["model_origin"] == "synthetic_scaled_collaboration"
        ]
        self.assertEqual(len(synthetic), 90)
        counts: dict[tuple[str, str], int] = {}
        for entry in synthetic:
            key = (entry["case"], entry["anomaly_type"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(set(counts.values()), {5})


if __name__ == "__main__":
    unittest.main()
