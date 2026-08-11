from __future__ import annotations

import unittest

from chapter7.logs import project_global_trace


class ProjectionTests(unittest.TestCase):
    def test_directional_projection(self) -> None:
        trace = ("M1_s", "M1_r", "M2", "unrelated")
        self.assertEqual(
            project_global_trace(trace, frozenset(("M1",)), frozenset(("M2",))),
            ("M1", "M2"),
        )
        self.assertEqual(
            project_global_trace(trace, frozenset(), frozenset(("M1", "M2"))),
            ("M1", "M2"),
        )


if __name__ == "__main__":
    unittest.main()
