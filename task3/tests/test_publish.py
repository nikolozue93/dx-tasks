"""Offline tests for Google publishing configuration and safety."""
import os
import unittest
from unittest.mock import patch

from src.publish import read_target_ids


class PublishConfigTests(unittest.TestCase):
    def test_both_ids_select_existing_targets(self):
        with patch.dict(os.environ, {"SHEET_ID": "sheet-id", "CSV_FILE_ID": "csv-id"}):
            self.assertEqual(read_target_ids(), ("sheet-id", "csv-id"))

    def test_both_missing_discover_by_name(self):
        with patch.dict(os.environ, {"SHEET_ID": "", "CSV_FILE_ID": ""}):
            self.assertEqual(read_target_ids(), (None, None))

    def test_only_one_id_is_rejected(self):
        with patch.dict(os.environ, {"SHEET_ID": "sheet-id", "CSV_FILE_ID": ""}):
            with self.assertRaisesRegex(ValueError, "both SHEET_ID and CSV_FILE_ID"):
                read_target_ids()


if __name__ == "__main__":
    unittest.main()
