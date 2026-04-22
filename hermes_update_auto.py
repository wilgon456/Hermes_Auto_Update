#!/usr/bin/env python3
"""Standalone daily updater for a Hermes git checkout."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
DEFAULT_CHANNEL = ""
DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass
class UpdateStatus:
    local_head: str
    remote_head: str
    behind_count: int
    commit_lines: list[str]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _short_sha(sha: str) -> str:
    return (sha or "")[:8]


def load_simple_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )


def collect_update_status(
    repo_root: Path,
    *,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
) -> UpdateStatus:
    run_git(repo_root, "fetch", remote)
    local_head = run_git(repo_root, "rev-parse", "HEAD").stdout.strip()
    remote_head = run_git(repo_root, "rev-parse", f"{remote}/{branch}").stdout.strip()
    behind_count = int(
        run_git(repo_root, "rev-list", f"HEAD..{remote}/{branch}", "--count").stdout.strip()
    )
    commit_lines: list[str] = []
    if behind_count > 0:
        limit = str(min(behind_count, 10))
        log_output = run_git(
            repo_root,
            "log",
            "--oneline",
            "--max-count",
            limit,
            f"HEAD..{remote}/{branch}",
        ).stdout
        commit_lines = [line.strip() for line in log_output.splitlines() if line.strip()]
    return UpdateStatus(
        local_head=local_head,
        remote_head=remote_head,
        behind_count=behind_count,
        commit_lines=commit_lines,
    )


def resolve_hermes_command(repo_root: Path) -> list[str]:
    windows = os.name == "nt"
    candidates = [
        repo_root / "venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
        repo_root / ".venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), "update"]
    raise FileNotFoundError("Could not find a Hermes executable in venv/ or .venv/")


def run_update(repo_root: Path, hermes_home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env_values = load_simple_env(hermes_home / ".env")
    for key, value in env_values.items():
        env.setdefault(key, value)
    cmd = resolve_hermes_command(repo_root)
    return subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )


def send_discord_message(channel_id: str, token: str, message: str) -> None:
    payload = json.dumps({"content": message}).encode("utf-8")
    req = request.Request(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        data=payload,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-update-auto/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"Discord API returned HTTP {resp.status}")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Discord API error {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Discord connection failed: {exc}") from exc


def tail_lines(text: str, max_lines: int = 20) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "(출력 없음)"
    return "\n".join(lines[-max_lines:])


def format_success(status_before: UpdateStatus, status_after: UpdateStatus, profile_name: str) -> str:
    lines = [
        "Hermes 일일 자동 업데이트 결과",
        "상태: 업데이트 성공",
        f"프로필: {profile_name}",
        f"호스트: {socket.gethostname()}",
        f"시각(UTC): {_now_utc()}",
        "저장소: NousResearch/hermes-agent",
        f"적용 커밋 수: {status_before.behind_count}",
        f"HEAD: {_short_sha(status_before.local_head)} -> {_short_sha(status_after.local_head)}",
    ]
    if status_before.commit_lines:
        lines.append("")
        lines.append("반영된 커밋:")
        lines.extend(f"- {line}" for line in status_before.commit_lines)
    return "\n".join(lines)


def format_update_failure(status_before: UpdateStatus, profile_name: str, output: str) -> str:
    return "\n".join(
        [
            "Hermes 일일 자동 업데이트 결과",
            "상태: 업데이트 실패",
            f"프로필: {profile_name}",
            f"호스트: {socket.gethostname()}",
            f"시각(UTC): {_now_utc()}",
            "저장소: NousResearch/hermes-agent",
            f"대기 중 커밋 수: {status_before.behind_count}",
            f"유지된 HEAD: {_short_sha(status_before.local_head)}",
            "",
            "최근 업데이트 출력:",
            tail_lines(output),
        ]
    )


def format_check_failure(profile_name: str, output: str) -> str:
    return "\n".join(
        [
            "Hermes 일일 자동 업데이트 결과",
            "상태: 업데이트 확인 실패",
            f"프로필: {profile_name}",
            f"호스트: {socket.gethostname()}",
            f"시각(UTC): {_now_utc()}",
            "",
            tail_lines(output),
        ]
    )


def format_no_update(profile_name: str, status: UpdateStatus) -> str:
    return "\n".join(
        [
            "Hermes 일일 자동 업데이트 결과",
            "상태: 업데이트 없음",
            f"프로필: {profile_name}",
            f"호스트: {socket.gethostname()}",
            f"시각(UTC): {_now_utc()}",
            f"현재 HEAD: {_short_sha(status.local_head)}",
        ]
    )


def run_once(config: dict[str, Any]) -> int:
    repo_root = Path(config["repo_root"]).expanduser().resolve()
    hermes_home = Path(config["hermes_home"]).expanduser().resolve()
    channel_id = str(config.get("discord_channel_id", DEFAULT_CHANNEL)).strip()
    remote = str(config.get("remote", DEFAULT_REMOTE))
    branch = str(config.get("branch", DEFAULT_BRANCH))
    notify_on_no_update = bool(config.get("notify_on_no_update", False))
    profile_name = hermes_home.name if hermes_home.name != ".hermes" else "default"

    env_values = load_simple_env(hermes_home / ".env")
    discord_token = env_values.get("DISCORD_BOT_TOKEN", "").strip()

    if not channel_id:
        print("discord_channel_id is missing in config.json", file=sys.stderr)
        return 2

    if not discord_token:
        print("DISCORD_BOT_TOKEN is missing in the Hermes profile .env", file=sys.stderr)
        return 2

    try:
        status_before = collect_update_status(repo_root, remote=remote, branch=branch)
    except subprocess.CalledProcessError as exc:
        output = "\n".join(filter(None, [exc.stdout, exc.stderr])) or str(exc)
        send_discord_message(channel_id, discord_token, format_check_failure(profile_name, output))
        print(output, file=sys.stderr)
        return 1

    if status_before.behind_count == 0:
        if notify_on_no_update:
            send_discord_message(channel_id, discord_token, format_no_update(profile_name, status_before))
        print("No updates available.")
        return 0

    update_proc = run_update(repo_root, hermes_home)
    update_output = "\n".join(filter(None, [update_proc.stdout, update_proc.stderr]))
    if update_proc.returncode != 0:
        send_discord_message(
            channel_id,
            discord_token,
            format_update_failure(status_before, profile_name, update_output),
        )
        print(update_output or "Update failed.", file=sys.stderr)
        return update_proc.returncode or 1

    status_after = collect_update_status(repo_root, remote=remote, branch=branch)
    send_discord_message(
        channel_id,
        discord_token,
        format_success(status_before, status_after, profile_name),
    )
    print(
        f"Updated {_short_sha(status_before.local_head)} -> {_short_sha(status_after.local_head)} "
        f"({status_before.behind_count} commits)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    return run_once(config)


if __name__ == "__main__":
    raise SystemExit(main())
