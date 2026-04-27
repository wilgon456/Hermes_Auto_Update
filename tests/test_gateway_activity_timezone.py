import json
import os
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import hermes_update_auto


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 4, 26, 20, 0, 0, tzinfo=timezone.utc)
        if tz is None:
            return fixed.replace(tzinfo=None)
        return fixed.astimezone(tz)


@contextmanager
def temporary_tz(name):
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = name
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        yield
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time, "tzset"):
            time.tzset()


class GatewayActivityTimezoneTests(unittest.TestCase):
    def test_naive_session_timestamps_are_interpreted_as_local_time(self):
        """A local KST evening session must not look recent at the 05:00 KST updater run."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "sessions").mkdir()
            (home / "gateway_state.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "gateway_state": "running",
                        "restart_requested": False,
                        "active_agents": 0,
                        "platforms": {},
                        "updated_at": "2026-04-26T11:19:02.522194+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (home / "sessions" / "sessions.json").write_text(
                json.dumps(
                    {
                        "agent:main:discord:group:test:user": {
                            "updated_at": "2026-04-26T23:09:20.514535",
                            "suspended": False,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with temporary_tz("Asia/Seoul"), patch.object(hermes_update_auto, "datetime", FixedDateTime):
                status = hermes_update_auto.load_recent_gateway_activity(
                    home,
                    session_window_seconds=120,
                )

        self.assertIsNone(status)

    def test_default_naive_parse_still_assumes_utc_for_non_session_callers(self):
        parsed = hermes_update_auto._parse_iso_datetime("2026-04-26T23:09:20")

        self.assertEqual(parsed, datetime(2026, 4, 26, 23, 9, 20, tzinfo=timezone.utc))

    def test_aware_parse_uses_embedded_offset(self):
        parsed = hermes_update_auto._parse_iso_datetime("2026-04-26T23:09:20+09:00")

        self.assertEqual(parsed, datetime(2026, 4, 26, 14, 9, 20, tzinfo=timezone.utc))
    def test_unusable_local_naive_session_timestamp_is_ignored(self):
        with temporary_tz("Asia/Seoul"):
            parsed = hermes_update_auto._parse_iso_datetime("0001-01-01T00:00:00", naive_tz=None)

        self.assertIsNone(parsed)


if __name__ == "__main__":
    unittest.main()
