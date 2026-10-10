import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from vulcan_messages import add_event_to_calendar

class TestCalendarPayload(unittest.TestCase):
    def test_missing_calendar_id(self):
        with patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": ""}, clear=True):
            success, msg = add_event_to_calendar({"summary": "Wycieczka", "start_date": "2026-10-15"})
            self.assertFalse(success)
            self.assertIn("Brak GOOGLE_CALENDAR_ID", msg)

    def test_missing_start_date(self):
        mock_sa_module = MagicMock()
        mock_req_module = MagicMock()
        with patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": "test_cal@group.calendar.google.com"}), \
             patch.dict(sys.modules, {
                 "google": MagicMock(),
                 "google.oauth2": mock_sa_module,
                 "google.oauth2.service_account": mock_sa_module,
                 "google.auth": mock_req_module,
                 "google.auth.transport": mock_req_module,
                 "google.auth.transport.requests": mock_req_module
             }), \
             patch("vulcan_messages.get_service_account_path") as mock_sa:
            mock_sa.return_value.exists.return_value = True
            mock_sa.return_value.is_file.return_value = True
            success, msg = add_event_to_calendar({"summary": "Brak daty"})
            self.assertFalse(success)
            self.assertIn("Brak daty", msg)

if __name__ == "__main__":
    unittest.main()
