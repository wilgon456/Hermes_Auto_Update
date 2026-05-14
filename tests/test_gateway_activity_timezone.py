import json
import os
import subprocess
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
    def _git(self, repo: Path, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=check,
        )

    def _make_diverged_repo(self, tmp: Path, *, conflict: bool = False) -> tuple[Path, Path]:
        origin = tmp / "origin.git"
        repo = tmp / "repo"
        upstream = tmp / "upstream"
        self._git(tmp, "init", "--bare", str(origin))
        self._git(tmp, "init", str(repo))
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test User")
        (repo / "file.txt").write_text("base\n", encoding="utf-8")
        self._git(repo, "add", "file.txt")
        self._git(repo, "commit", "-m", "base")
        self._git(repo, "branch", "-M", "main")
        self._git(repo, "remote", "add", "origin", str(origin))
        self._git(repo, "push", "-u", "origin", "main")

        self._git(tmp, "clone", str(origin), str(upstream))
        self._git(upstream, "config", "user.email", "test@example.com")
        self._git(upstream, "config", "user.name", "Test User")

        if conflict:
            (repo / "file.txt").write_text("local\n", encoding="utf-8")
            self._git(repo, "add", "file.txt")
            self._git(repo, "commit", "-m", "local change")
            (upstream / "file.txt").write_text("upstream\n", encoding="utf-8")
            self._git(upstream, "add", "file.txt")
            self._git(upstream, "commit", "-m", "upstream change")
        else:
            (repo / "local.txt").write_text("local\n", encoding="utf-8")
            self._git(repo, "add", "local.txt")
            self._git(repo, "commit", "-m", "local change")
            (upstream / "upstream.txt").write_text("upstream\n", encoding="utf-8")
            self._git(upstream, "add", "upstream.txt")
            self._git(upstream, "commit", "-m", "upstream change")

        self._git(upstream, "push", "origin", "main")
        self._git(repo, "fetch", "origin")
        return repo, tmp / "hermes-home"

    def test_ignored_untracked_paths_do_not_block_dirty_check(self):
        status = hermes_update_auto.filter_ignored_worktree_status(
            hermes_update_auto.WorktreeStatus(
                dirty=True,
                lines=["?? plans/README.md", "?? plans/phaseA.log"],
            ),
            ignored_worktree_paths=[],
            ignored_untracked_paths=["plans/**"],
        )

        self.assertFalse(status.dirty)
        self.assertEqual(status.lines, [])
        self.assertEqual(status.ignored_lines, ["?? plans/README.md", "?? plans/phaseA.log"])

    def test_local_commit_sync_replays_local_patch_on_updated_upstream(self):
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo, hermes_home = self._make_diverged_repo(tmp)
            hermes_home.mkdir()

            result = hermes_update_auto.run_local_commit_sync(
                repo,
                hermes_home,
                {},
                remote="origin",
                branch="main",
            )

            self.assertEqual(len(result.applied_patches), 1)
            self.assertTrue((repo / "local.txt").exists())
            self.assertTrue((repo / "upstream.txt").exists())
            self.assertTrue(result.backup_dir.exists())
            self.assertEqual(
                self._git(repo, "rev-list", "HEAD..origin/main", "--count").stdout.strip(),
                "0",
            )
            self.assertEqual(
                self._git(repo, "rev-list", "origin/main..HEAD", "--count").stdout.strip(),
                "1",
            )

    def test_local_commit_sync_conflict_rolls_back_to_backup_branch(self):
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo, hermes_home = self._make_diverged_repo(tmp, conflict=True)
            hermes_home.mkdir()
            original_head = self._git(repo, "rev-parse", "HEAD").stdout.strip()

            with self.assertRaises(hermes_update_auto.LocalCommitSyncConflict) as raised:
                hermes_update_auto.run_local_commit_sync(
                    repo,
                    hermes_home,
                    {},
                    remote="origin",
                    branch="main",
                )

            self.assertEqual(self._git(repo, "rev-parse", "HEAD").stdout.strip(), original_head)
            self.assertIn("file.txt", raised.exception.conflicted_files)
            self.assertTrue(raised.exception.backup_dir.exists())

    def test_local_commit_sync_allows_prior_upstream_merge_commit(self):
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            origin = tmp / "origin.git"
            repo = tmp / "repo"
            upstream = tmp / "upstream"
            self._git(tmp, "init", "--bare", str(origin))
            self._git(tmp, "init", str(repo))
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "Test User")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            self._git(repo, "add", "base.txt")
            self._git(repo, "commit", "-m", "base")
            self._git(repo, "branch", "-M", "main")
            self._git(repo, "remote", "add", "origin", str(origin))
            self._git(repo, "push", "-u", "origin", "main")

            self._git(tmp, "clone", str(origin), str(upstream))
            self._git(upstream, "config", "user.email", "test@example.com")
            self._git(upstream, "config", "user.name", "Test User")
            (upstream / "upstream-old.txt").write_text("old\n", encoding="utf-8")
            self._git(upstream, "add", "upstream-old.txt")
            self._git(upstream, "commit", "-m", "upstream old")
            self._git(upstream, "push", "origin", "main")

            (repo / "local.txt").write_text("local\n", encoding="utf-8")
            self._git(repo, "add", "local.txt")
            self._git(repo, "commit", "-m", "local change")
            self._git(repo, "fetch", "origin")
            self._git(repo, "merge", "--no-edit", "origin/main")

            (upstream / "upstream-new.txt").write_text("new\n", encoding="utf-8")
            self._git(upstream, "add", "upstream-new.txt")
            self._git(upstream, "commit", "-m", "upstream new")
            self._git(upstream, "push", "origin", "main")
            self._git(repo, "fetch", "origin")

            hermes_home = tmp / "hermes-home"
            hermes_home.mkdir()
            result = hermes_update_auto.run_local_commit_sync(
                repo,
                hermes_home,
                {},
                remote="origin",
                branch="main",
            )

            self.assertTrue((repo / "local.txt").exists())
            self.assertTrue((repo / "upstream-old.txt").exists())
            self.assertTrue((repo / "upstream-new.txt").exists())
            self.assertTrue((result.backup_dir / "dropped-upstream-merge-commits.txt").exists())

    def test_run_once_uses_post_git_maintenance_after_local_commit_sync(self):
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo, hermes_home = self._make_diverged_repo(tmp)
            hermes_home.mkdir()
            config = {
                "repo_root": str(repo),
                "hermes_home": str(hermes_home),
                "discord_channel_id": "test-channel",
                "remote": "origin",
                "branch": "main",
                "defer_if_repo_dirty": True,
                "defer_if_recent_gateway_activity": False,
                "defer_if_local_commits": False,
                "auto_update_official_skills": False,
                "auto_update_custom_skills": False,
                "notify_on_no_update": False,
            }

            with (
                patch("hermes_update_auto.send_discord_message"),
                patch("hermes_update_auto.run_update") as run_update,
                patch("hermes_update_auto.run_post_git_maintenance") as maintenance,
                patch("hermes_update_auto.run_pip_check") as pip_check,
            ):
                maintenance.return_value = subprocess.CompletedProcess(
                    ["maintenance"],
                    0,
                    "maintenance ok",
                    "",
                )
                pip_check.return_value = subprocess.CompletedProcess(
                    ["pip", "check"],
                    0,
                    "No broken requirements found.",
                    "",
                )

                result = hermes_update_auto.run_once(config)

            self.assertEqual(result, 0)
            run_update.assert_not_called()
            maintenance.assert_called_once()
            self.assertTrue((repo / "local.txt").exists())
            self.assertTrue((repo / "upstream.txt").exists())

    def test_codex_handoff_writes_prompt_and_invokes_codex_exec(self):
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo = tmp / "repo"
            backup_dir = tmp / "patches"
            hermes_home = tmp / "home"
            repo.mkdir()
            backup_dir.mkdir()
            hermes_home.mkdir()
            patch_file = backup_dir / "0001-local.patch"
            patch_file.write_text("Subject: [PATCH] local\n", encoding="utf-8")
            exc = hermes_update_auto.LocalCommitSyncConflict(
                timestamp="20260514-010203",
                backup_branch="backup/hermes-auto-update-20260514-010203",
                backup_dir=backup_dir,
                patch_file=patch_file,
                patch_subject="local",
                conflicted_files=["tests/example.py"],
                output="conflict",
            )
            config = {
                "codex_conflict_handoff": {
                    "enabled": True,
                    "command": "codex",
                    "timeout_seconds": 60,
                    "sandbox": "workspace-write",
                    "approval": "never",
                    "log_dir": str(tmp / "handoff"),
                }
            }

            with patch("hermes_update_auto.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    ["codex"],
                    0,
                    "codex ok",
                    "",
                )
                result = hermes_update_auto.run_codex_conflict_handoff(
                    repo,
                    hermes_home,
                    config,
                    remote="origin",
                    branch="main",
                    profile_name="main",
                    exc=exc,
                )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.prompt_file.exists())
            self.assertTrue(result.output_file.exists())
            self.assertIn("Do not push", result.prompt_file.read_text(encoding="utf-8"))
            cmd = run.call_args.args[0]
            self.assertIn("exec", cmd)
            self.assertIn("--cd", cmd)
            self.assertIn(str(repo), cmd)
            self.assertIn("--add-dir", cmd)
            self.assertIn(str(backup_dir), cmd)
            self.assertIn("--ask-for-approval", cmd)
            self.assertIn("never", cmd)

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

    def test_registered_local_overlay_matches_all_current_dirty_paths(self):
        overlays = hermes_update_auto.load_local_overlays(
            {
                "local_overlays": [
                    {
                        "id": "discord-channel-model-bindings",
                        "paths": [
                            "gateway/platforms/discord.py",
                            "gateway/run.py",
                            "tests/gateway/test_discord_channel_controls.py",
                            "tests/gateway/test_discord_channel_models.py",
                        ],
                    }
                ]
            }
        )
        status = hermes_update_auto.WorktreeStatus(
            dirty=True,
            lines=[
                " M gateway/platforms/discord.py",
                " M gateway/run.py",
                " M tests/gateway/test_discord_channel_controls.py",
                "?? tests/gateway/test_discord_channel_models.py",
            ],
        )

        active, unknown = hermes_update_auto.match_local_overlays(status, overlays)

        self.assertEqual([overlay.overlay_id for overlay in active], ["discord-channel-model-bindings"])
        self.assertEqual(unknown, [])

    def test_local_overlay_deferred_when_unknown_path_is_mixed_in(self):
        overlays = hermes_update_auto.load_local_overlays(
            {
                "local_overlays": [
                    {
                        "id": "discord-channel-model-bindings",
                        "paths": ["gateway/run.py"],
                    }
                ]
            }
        )
        status = hermes_update_auto.WorktreeStatus(
            dirty=True,
            lines=[" M gateway/run.py", " M pyproject.toml"],
        )

        active, unknown = hermes_update_auto.match_local_overlays(status, overlays)

        self.assertEqual([overlay.overlay_id for overlay in active], ["discord-channel-model-bindings"])
        self.assertEqual(unknown, ["pyproject.toml"])


if __name__ == "__main__":
    unittest.main()
