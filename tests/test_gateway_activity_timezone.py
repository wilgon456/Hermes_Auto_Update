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


class HermesCommandTimeoutTests(unittest.TestCase):
    def test_run_hermes_command_times_out_and_terminates_process_group(self):
        class FakeProcess:
            pid = 12345
            returncode = None

            def __init__(self):
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise hermes_update_auto.subprocess.TimeoutExpired(
                        cmd=["hermes", "update"],
                        timeout=timeout,
                    )
                self.returncode = -15
                return "out", "err"

        fake_proc = FakeProcess()

        with (
            TemporaryDirectory() as tmp,
            patch("hermes_update_auto.resolve_hermes_command", return_value=["hermes", "update"]),
            patch("hermes_update_auto.load_simple_env", return_value={}),
            patch("hermes_update_auto.subprocess.Popen", return_value=fake_proc) as popen,
            patch("hermes_update_auto.os.killpg") as killpg,
        ):
            result = hermes_update_auto.run_hermes_command(
                Path(tmp),
                Path(tmp),
                "update",
                timeout_seconds=1,
            )

        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out after 1s", result.stderr)
        killpg.assert_called_once_with(12345, hermes_update_auto.signal.SIGTERM)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_run_hermes_command_timeout_kills_stubborn_process_group(self):
        class FakeProcess:
            pid = 23456
            returncode = None

            def communicate(self, timeout=None):
                if timeout is not None:
                    raise hermes_update_auto.subprocess.TimeoutExpired(
                        cmd=["hermes", "update"],
                        timeout=timeout,
                    )
                self.returncode = -9
                return "", ""

        with (
            TemporaryDirectory() as tmp,
            patch("hermes_update_auto.resolve_hermes_command", return_value=["hermes", "update"]),
            patch("hermes_update_auto.load_simple_env", return_value={}),
            patch("hermes_update_auto.subprocess.Popen", return_value=FakeProcess()),
            patch("hermes_update_auto.os.killpg") as killpg,
        ):
            result = hermes_update_auto.run_hermes_command(
                Path(tmp),
                Path(tmp),
                "update",
                timeout_seconds=1,
            )

        self.assertEqual(result.returncode, 124)
        self.assertEqual(
            killpg.call_args_list,
            [
                ((23456, hermes_update_auto.signal.SIGTERM),),
                ((23456, hermes_update_auto.signal.SIGKILL),),
            ],
        )


class UpdateSafetyTests(unittest.TestCase):
    def test_worktree_deferred_message_warns_against_stash_restore_conflicts(self):
        message = hermes_update_auto.format_worktree_deferred(
            "main",
            hermes_update_auto.WorktreeStatus(
                dirty=True,
                lines=["M gateway/run.py"],
            ),
        )

        self.assertIn("권장 조치", message)
        self.assertIn("upstream", message)
        self.assertIn("stash/restore 충돌", message)

    def test_run_pip_check_timeout_returns_failure_result(self):
        with (
            TemporaryDirectory() as tmp,
            patch("hermes_update_auto.resolve_hermes_python", return_value="/venv/bin/python"),
            patch("hermes_update_auto.load_simple_env", return_value={}),
            patch("hermes_update_auto.subprocess.run") as run,
        ):
            run.side_effect = hermes_update_auto.subprocess.TimeoutExpired(
                cmd=["/venv/bin/python", "-m", "pip", "check"],
                timeout=1,
                output="",
                stderr="",
            )

            result = hermes_update_auto.run_pip_check(
                Path(tmp),
                Path(tmp),
                timeout_seconds=1,
            )

        self.assertEqual(result.returncode, 124)
        self.assertIn("pip check timed out after 1s", result.stderr)

    def test_local_commits_deferred_message_warns_against_divergence(self):
        message = hermes_update_auto.format_local_commits_deferred(
            "main",
            hermes_update_auto.UpdateStatus(
                local_head="abcdef123456",
                remote_head="123456abcdef",
                behind_count=0,
                ahead_count=2,
                commit_lines=[],
            ),
        )

        self.assertIn("로컬 전용 커밋 수: 2", message)
        self.assertIn("upstream에 없는 로컬 커밋", message)
        self.assertIn("merge/rebase 충돌", message)


if __name__ == "__main__":
    unittest.main()
