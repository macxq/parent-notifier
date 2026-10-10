import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import vulcan_messages

class TestStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_seen(self):
        test_file = self.data_dir / "seen_test.json"
        with patch.object(vulcan_messages, "SEEN_FILE", test_file):
            # Początkowo pusty zbiór
            self.assertEqual(vulcan_messages.load_seen(), set())

            # Zapis ID
            sample_ids = {101, 102, 103}
            vulcan_messages.save_seen(sample_ids)

            # Odczyt
            loaded = vulcan_messages.load_seen()
            self.assertEqual(loaded, sample_ids)

    def test_save_and_load_calendar_events(self):
        test_file = self.data_dir / "cal_test.json"
        with patch.object(vulcan_messages, "CALENDAR_EVENTS_FILE", test_file):
            # Początkowo pusty
            self.assertEqual(vulcan_messages.load_added_calendar_events(), set())

            # Zapis kluczy wydarzeń
            sample_events = {"2026-10-15_Wycieczka", "2026-10-20_Zebranie"}
            vulcan_messages.save_added_calendar_events(sample_events)

            # Odczyt
            loaded = vulcan_messages.load_added_calendar_events()
            self.assertEqual(loaded, sample_events)

    def test_resolve_data_file_migration(self):
        legacy_dir = self.data_dir / "legacy"
        target_dir = self.data_dir / "target"
        legacy_dir.mkdir()
        target_dir.mkdir()

        # Tworzymy plik w legacy
        legacy_file = legacy_dir / "test_state.json"
        legacy_file.write_text('{"migrated": true}', encoding="utf-8")

        with patch.object(vulcan_messages, "BASE_DIR", legacy_dir), \
             patch.object(vulcan_messages, "DATA_DIR", target_dir):
            resolved = vulcan_messages.resolve_data_file("test_state.json")
            self.assertEqual(resolved, target_dir / "test_state.json")
            self.assertTrue(resolved.exists())
            self.assertIn("migrated", resolved.read_text(encoding="utf-8"))

    def test_get_service_account_path(self):
        target_dir = self.data_dir / "target"
        target_dir.mkdir()
        sa_file = target_dir / "service_account.json"
        sa_file.write_text('{"type": "service_account"}', encoding="utf-8")

        with patch.object(vulcan_messages, "DATA_DIR", target_dir), \
             patch.dict("os.environ", {}, clear=True):
            resolved = vulcan_messages.get_service_account_path()
            self.assertEqual(resolved, sa_file)

if __name__ == "__main__":
    unittest.main()
