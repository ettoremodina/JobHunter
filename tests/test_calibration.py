"""Calibration evidence validation must fail safely before learning exclusions."""

import unittest
from jobhunter.experiments.calibration import validate


class CalibrationTests(unittest.TestCase):
    """Keep identity integrity strict and unsupported model conclusions reviewable."""

    def test_unquoted_rejection_is_review(self):
        """An invented requirement cannot become an automatic exclusion."""
        job = {"id": "a", "title": "Data Scientist", "description": "Junior role"}
        result = validate({"labels": [{"id": "a", "decision": "exclude", "evidence": "10 years required", "confidence": "high"}]}, [job])
        self.assertEqual(result[0]["decision"], "review")
        self.assertFalse(result[0]["evidence_valid"])
        with self.assertRaises(ValueError):
            validate({"labels": []}, [job])

