#!/usr/bin/env python3
"""Standalone daily updater for a Hermes git checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

if os.name == "nt":
    import msvcrt
else:
    import fcntl


DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
DEFAULT_CHANNEL = ""
DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_MANUAL_SKILLS_MANIFEST = "tracked-public-skills.json"
DEFAULT_AUTO_UPDATE_OFFICIAL_SKILLS = True
DEFAULT_AUTO_UPDATE_CUSTOM_SKILLS = True
DEFAULT_AUTO_DISCOVER_MANUAL_PUBLIC_SKILLS = True
DEFAULT_DEFER_IF_RECENT_GATEWAY_ACTIVITY = True
DEFAULT_DEFER_IF_REPO_DIRTY = True
DEFAULT_RECENT_SESSION_WINDOW_SECONDS = 120
DEFAULT_GATEWAY_RUNTIME_STATUS_FILE = "gateway_state.json"
DEFAULT_GATEWAY_SESSIONS_DIR = "sessions"
DEFAULT_GATEWAY_SESSIONS_INDEX = "sessions.json"
BUSY_GATEWAY_STATES = {"starting", "draining", "stopping", "restarting"}
_MANUAL_UPDATED_RE = re.compile(r"Installed:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class UpdateStatus:
    local_head: str
    remote_head: str
    behind_count: int
    commit_lines: list[str]


@dataclass
class SkillUpdateStatus:
    checked: bool
    updated_count: int
    updated_names: list[str]
    output: str


@dataclass
class TrackedSkill:
    identifier: str
    name: str
    category: str
    install_path: str
    enabled: bool
    state: dict[str, Any]
    raw: dict[str, Any]


@dataclass
class TrackedSkillCheck:
    ok: bool
    status: str
    name: str
    install_path: str
    remote_hash: str
    local_hash: str
    output: str


@dataclass
class HubSkillCandidate:
    name: str
    identifier: str
    source: str
    install_path: str


@dataclass
class GatewayActivityStatus:
    profile_name: str
    hermes_home: Path
    pid: int
    pid_running: bool
    gateway_state: str
    restart_requested: bool
    active_agents: int
    recent_session_count: int
    recently_updated_platforms: list[str]
    blocker_reasons: list[str]
    runtime_updated_at: str
    recent_session_updated_at: str


@dataclass
class WorktreeStatus:
    dirty: bool
    lines: list[str]


class AlreadyRunningError(RuntimeError):
    """Raised when another updater instance already holds the run lock."""


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


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_lock_path(config: dict[str, Any]) -> Path:
    config_path = Path(config.get("_config_path", __file__)).expanduser().resolve()
    repo_root = Path(config["repo_root"]).expanduser().resolve()
    hermes_home = Path(config["hermes_home"]).expanduser().resolve()
    scope = f"{repo_root}\n{hermes_home}"
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    return config_path.with_name(f".hermes-update-auto.{digest}.lock")


def acquire_run_lock(lock_path: Path) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("\n")
            lock_file.flush()
        lock_file.seek(0)

        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.seek(0)
        holder = lock_file.read().strip()
        lock_file.close()
        detail = f" Holder: {holder}" if holder else ""
        raise AlreadyRunningError(
            f"Another hermes-update-auto process is already running for this repo/profile."
            f" Lock: {lock_path}.{detail}"
        ) from exc

    metadata = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at_utc": _now_utc(),
    }
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(json.dumps(metadata, ensure_ascii=False) + "\n")
    lock_file.flush()
    return lock_file


def release_run_lock(lock_file: Any) -> None:
    try:
        if os.name == "nt":
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _parse_iso_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def iter_hermes_homes(hermes_home: Path) -> list[Path]:
    candidates: list[Path] = []
    if hermes_home.parent.name == "profiles":
        candidates.extend(path for path in hermes_home.parent.iterdir() if path.is_dir())
    else:
        candidates.append(hermes_home)
        profiles_dir = hermes_home / "profiles"
        if profiles_dir.is_dir():
            candidates.extend(path for path in profiles_dir.iterdir() if path.is_dir())

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in [hermes_home, *candidates]:
        resolved = path.expanduser().resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def load_recent_gateway_activity(
    hermes_home: Path,
    *,
    session_window_seconds: int,
) -> GatewayActivityStatus | None:
    runtime_path = hermes_home / DEFAULT_GATEWAY_RUNTIME_STATUS_FILE
    try:
        runtime_payload = read_json_file(runtime_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(runtime_payload, dict):
        return None

    pid = _safe_int(runtime_payload.get("pid", 0) or 0)
    pid_running = _pid_is_running(pid)
    if not pid_running:
        return None

    gateway_state = str(runtime_payload.get("gateway_state", "")).strip()
    normalized_gateway_state = gateway_state.lower()
    restart_requested = bool(runtime_payload.get("restart_requested", False))
    active_agents = _safe_int(runtime_payload.get("active_agents", 0) or 0)
    runtime_updated_at = str(runtime_payload.get("updated_at", "")).strip()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(0, session_window_seconds))
    recent_session_count = 0
    recent_session_updated_at = ""
    sessions_index_path = hermes_home / DEFAULT_GATEWAY_SESSIONS_DIR / DEFAULT_GATEWAY_SESSIONS_INDEX
    try:
        sessions_payload = read_json_file(sessions_index_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        sessions_payload = {}

    if isinstance(sessions_payload, dict):
        newest: datetime | None = None
        for entry in sessions_payload.values():
            if not isinstance(entry, dict):
                continue
            updated_at = _parse_iso_datetime(str(entry.get("updated_at", "")))
            if updated_at is None or updated_at < cutoff:
                continue
            if bool(entry.get("suspended", False)):
                continue
            recent_session_count += 1
            if newest is None or updated_at > newest:
                newest = updated_at
        if newest is not None:
            recent_session_updated_at = newest.strftime("%Y-%m-%d %H:%M:%SZ")

    recently_updated_platforms: list[str] = []
    platforms = runtime_payload.get("platforms", {})
    if isinstance(platforms, dict):
        for name, payload in platforms.items():
            if not isinstance(payload, dict):
                continue
            state = str(payload.get("state", "")).strip().lower()
            updated_at = _parse_iso_datetime(str(payload.get("updated_at", "")))
            if state == "connected" and updated_at is not None and updated_at >= cutoff:
                recently_updated_platforms.append(str(name))

    blocker_reasons: list[str] = []
    if active_agents > 0:
        blocker_reasons.append("active_agents")
    if recent_session_count > 0:
        blocker_reasons.append("recent_sessions")
    if recently_updated_platforms:
        blocker_reasons.append("recent_platform_activity")
    if restart_requested:
        blocker_reasons.append("restart_requested")
    if normalized_gateway_state in BUSY_GATEWAY_STATES:
        blocker_reasons.append(f"gateway_state={normalized_gateway_state}")

    if not blocker_reasons:
        return None

    profile_name = hermes_home.name if hermes_home.name != ".hermes" else "default"
    return GatewayActivityStatus(
        profile_name=profile_name,
        hermes_home=hermes_home,
        pid=pid,
        pid_running=pid_running,
        gateway_state=gateway_state,
        restart_requested=restart_requested,
        active_agents=active_agents,
        recent_session_count=recent_session_count,
        recently_updated_platforms=recently_updated_platforms,
        blocker_reasons=blocker_reasons,
        runtime_updated_at=runtime_updated_at,
        recent_session_updated_at=recent_session_updated_at,
    )


def detect_recent_gateway_activity(
    hermes_home: Path,
    *,
    session_window_seconds: int,
) -> list[GatewayActivityStatus]:
    blockers: list[GatewayActivityStatus] = []
    for candidate in iter_hermes_homes(hermes_home):
        status = load_recent_gateway_activity(
            candidate,
            session_window_seconds=session_window_seconds,
        )
        if status is None:
            continue
        blockers.append(status)
    return blockers


def run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )


def collect_worktree_status(repo_root: Path) -> WorktreeStatus:
    proc = run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return WorktreeStatus(dirty=bool(lines), lines=lines)


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


def resolve_hermes_command(repo_root: Path, *args: str) -> list[str]:
    windows = os.name == "nt"
    candidates = [
        repo_root / "venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
        repo_root / ".venv" / ("Scripts" if windows else "bin") / ("hermes.exe" if windows else "hermes"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), *args]
    raise FileNotFoundError("Could not find a Hermes executable in venv/ or .venv/")


def resolve_hermes_python(repo_root: Path) -> str:
    windows = os.name == "nt"
    candidates = [
        repo_root / "venv" / ("Scripts" if windows else "bin") / ("python.exe" if windows else "python"),
        repo_root / ".venv" / ("Scripts" if windows else "bin") / ("python.exe" if windows else "python"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("Could not find a Python executable in venv/ or .venv/")


def run_hermes_command(
    repo_root: Path,
    hermes_home: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env_values = load_simple_env(hermes_home / ".env")
    for key, value in env_values.items():
        env.setdefault(key, value)
    cmd = resolve_hermes_command(repo_root, *args)
    return subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )


def run_hermes_python(
    repo_root: Path,
    hermes_home: Path,
    code: str,
    payload: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env_values = load_simple_env(hermes_home / ".env")
    for key, value in env_values.items():
        env.setdefault(key, value)
    return subprocess.run(
        [resolve_hermes_python(repo_root), "-c", code, json.dumps(payload, ensure_ascii=False)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )


def run_update(repo_root: Path, hermes_home: Path) -> subprocess.CompletedProcess[str]:
    return run_hermes_command(repo_root, hermes_home, "update")


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


def combine_skill_statuses(*statuses: SkillUpdateStatus) -> SkillUpdateStatus:
    checked = any(status.checked for status in statuses)
    updated_count = sum(status.updated_count for status in statuses)
    updated_names: list[str] = []
    outputs: list[str] = []
    for status in statuses:
        updated_names.extend(status.updated_names)
        if status.output.strip():
            outputs.append(status.output.strip())
    return SkillUpdateStatus(
        checked=checked,
        updated_count=updated_count,
        updated_names=updated_names,
        output="\n\n".join(outputs),
    )


def default_manual_skills_manifest(config_path: Path) -> Path:
    return config_path.with_name(DEFAULT_MANUAL_SKILLS_MANIFEST)


def resolve_update_toggle(
    config: dict[str, Any],
    primary_key: str,
    *,
    legacy_key: str = "auto_update_public_skills",
    default: bool,
) -> bool:
    if primary_key in config:
        return bool(config.get(primary_key))
    if legacy_key in config:
        return bool(config.get(legacy_key))
    return default


def _derive_name_from_identifier(identifier: str) -> str:
    value = identifier.strip().rstrip("/")
    if not value:
        return ""
    if ":" in value:
        value = value.split(":", 1)[1]
    return value.split("/")[-1]


def _normalize_category(category: str) -> str:
    return category.strip().strip("/")


def load_tracked_skills(manifest_path: Path) -> tuple[list[TrackedSkill], dict[str, Any]]:
    if not manifest_path.exists():
        return [], {"version": 1, "skills": []}

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        root: dict[str, Any] = {"version": 1, "skills": data}
    elif isinstance(data, dict):
        root = data
    else:
        raise ValueError(f"Unsupported manifest format: {manifest_path}")

    entries = root.get("skills", [])
    if not isinstance(entries, list):
        raise ValueError(f"'skills' must be a list in {manifest_path}")

    tracked: list[TrackedSkill] = []
    normalized_entries: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError(f"Skill entries must be objects in {manifest_path}")

        identifier = str(raw.get("identifier", "")).strip()
        if not identifier:
            raise ValueError(f"Skill entry is missing 'identifier' in {manifest_path}")

        name = str(raw.get("name", "")).strip() or _derive_name_from_identifier(identifier)
        category = _normalize_category(str(raw.get("category", "")))
        install_path = str(raw.get("install_path", "")).strip().strip("/")
        if not install_path:
            install_path = "/".join(part for part in (category, name) if part)
        if not install_path:
            raise ValueError(f"Skill entry could not resolve install_path for '{identifier}'")

        state = raw.get("state", {})
        if not isinstance(state, dict):
            state = {}

        normalized = dict(raw)
        normalized["name"] = name
        normalized["category"] = category
        normalized["install_path"] = install_path
        normalized["enabled"] = bool(raw.get("enabled", True))
        normalized["state"] = state
        normalized_entries.append(normalized)

        tracked.append(
            TrackedSkill(
                identifier=identifier,
                name=name,
                category=category,
                install_path=install_path,
                enabled=bool(raw.get("enabled", True)),
                state=state,
                raw=normalized,
            )
        )

    normalized_root = dict(root)
    normalized_root["version"] = int(root.get("version", 1) or 1)
    normalized_root["skills"] = normalized_entries
    return tracked, normalized_root


_CHECK_HUB_SKILLS_SNIPPET = r"""
import json
import os
import sys
from pathlib import Path


def load_simple_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


payload = json.loads(sys.argv[1])
repo_root = Path(payload["repo_root"]).resolve()
hermes_home = Path(payload["hermes_home"]).resolve()
mode = payload["mode"]
os.environ["HERMES_HOME"] = str(hermes_home)
load_simple_env(hermes_home / ".env")
sys.path.insert(0, str(repo_root))

from tools.skills_hub import HubLockFile, check_for_skill_updates

installed = HubLockFile().list_installed()
if mode == "official":
    installed = [entry for entry in installed if entry.get("source") == "official"]
elif mode == "custom":
    installed = [entry for entry in installed if entry.get("source") != "official"]

lock = HubLockFile()
lock.list_installed = lambda: installed
results = check_for_skill_updates(lock=lock)

updates = []
for entry in results:
    if entry.get("status") != "update_available":
        continue

    name = str(entry.get("name", "")).strip()
    installed_entry = next((item for item in installed if item.get("name") == name), None)
    updates.append({
        "name": name,
        "identifier": str(entry.get("identifier", "")).strip(),
        "source": str(entry.get("source", "")).strip(),
        "install_path": str((installed_entry or {}).get("install_path", "")).strip(),
    })

print(json.dumps({
    "updates": updates,
    "checked_count": len(installed),
}, ensure_ascii=False))
"""


def category_from_install_path(install_path: str) -> str:
    normalized = install_path.strip().strip("/")
    if not normalized or "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0]


def check_hub_skill_updates(
    repo_root: Path,
    hermes_home: Path,
    *,
    mode: str,
) -> tuple[list[HubSkillCandidate], int]:
    proc = run_hermes_python(
        repo_root,
        hermes_home,
        _CHECK_HUB_SKILLS_SNIPPET,
        {
            "repo_root": str(repo_root),
            "hermes_home": str(hermes_home),
            "mode": mode,
        },
    )
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if proc.returncode != 0:
        raise RuntimeError(output or f"Hub skill update probe failed ({mode}).")

    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        raise RuntimeError(output or f"Hub skill update probe produced invalid JSON ({mode}).")

    updates: list[HubSkillCandidate] = []
    for entry in payload.get("updates", []):
        if not isinstance(entry, dict):
            continue
        updates.append(
            HubSkillCandidate(
                name=str(entry.get("name", "")).strip(),
                identifier=str(entry.get("identifier", "")).strip(),
                source=str(entry.get("source", "")).strip(),
                install_path=str(entry.get("install_path", "")).strip(),
            )
        )
    checked_count = int(payload.get("checked_count", 0) or 0)
    return updates, checked_count


def update_hub_skills(
    repo_root: Path,
    hermes_home: Path,
    *,
    mode: str,
) -> SkillUpdateStatus:
    updates, checked_count = check_hub_skill_updates(repo_root, hermes_home, mode=mode)
    updated_names: list[str] = []
    outputs: list[str] = []

    for candidate in updates:
        args = ["skills", "install", candidate.identifier, "--force", "--yes"]
        category = category_from_install_path(candidate.install_path)
        if category:
            args.extend(["--category", category])
        install_proc = run_hermes_command(repo_root, hermes_home, *args)
        install_output = "\n".join(filter(None, [install_proc.stdout, install_proc.stderr]))
        outputs.append(install_output.strip())
        if install_proc.returncode != 0 or not parse_manual_install_success(install_output):
            raise RuntimeError(
                f"Hub skill update failed for '{candidate.name or candidate.identifier}': "
                f"{tail_lines(install_output or 'install command failed')}"
            )
        updated_names.append(candidate.name or _derive_name_from_identifier(candidate.identifier))

    return SkillUpdateStatus(
        checked=checked_count > 0,
        updated_count=len(updated_names),
        updated_names=updated_names,
        output="\n\n".join(text for text in outputs if text),
    )


_DISCOVER_MANUAL_SKILLS_SNIPPET = r"""
import json
import os
import sys
from pathlib import Path


def load_simple_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


payload = json.loads(sys.argv[1])
repo_root = Path(payload["repo_root"]).resolve()
hermes_home = Path(payload["hermes_home"]).resolve()
os.environ["HERMES_HOME"] = str(hermes_home)
load_simple_env(hermes_home / ".env")
sys.path.insert(0, str(repo_root))

from agent.skill_utils import EXCLUDED_SKILL_DIRS, parse_frontmatter
from tools.skills_hub import GitHubAuth, HubLockFile, create_source_router, unified_search
from tools.skills_sync import _read_manifest

skills_dir = hermes_home / "skills"
tracked_paths = set(payload.get("tracked_paths", []))
hub_paths = {
    str(entry.get("install_path", "")).strip().strip("/")
    for entry in HubLockFile().list_installed()
}
bundled_names = set(_read_manifest().keys())
sources = create_source_router(GitHubAuth())

discovered = []
skipped = []

for skill_md in skills_dir.rglob("SKILL.md"):
    if any(part in EXCLUDED_SKILL_DIRS for part in skill_md.parts):
        continue

    skill_dir = skill_md.parent
    rel_install_path = str(skill_dir.relative_to(skills_dir)).strip().strip("/")
    if not rel_install_path:
        continue
    if rel_install_path in tracked_paths or rel_install_path in hub_paths:
        continue

    try:
        content = skill_md.read_text(encoding="utf-8", errors="ignore")[:12000]
    except OSError:
        continue

    frontmatter, _body = parse_frontmatter(content)
    name = str(frontmatter.get("name") or skill_dir.name).strip()
    if not name:
        continue
    if name in bundled_names:
        continue

    results = unified_search(name, sources, source_filter="all", limit=20)
    exact = []
    seen_identifiers = set()
    for result in results:
        result_name = str(getattr(result, "name", "")).strip()
        identifier = str(getattr(result, "identifier", "")).strip()
        if not identifier or result_name.lower() != name.lower():
            continue
        if identifier in seen_identifiers:
            continue
        seen_identifiers.add(identifier)
        exact.append(result)

    category = str(skill_dir.relative_to(skills_dir).parent)
    category = "" if category == "." else category

    if len(exact) == 1:
        discovered.append({
            "identifier": exact[0].identifier,
            "name": name,
            "category": category,
            "install_path": rel_install_path,
            "enabled": True,
            "auto_discovered": True,
            "state": {
                "discovered_at": payload.get("now_utc", ""),
                "discovery_method": "unique_exact_name",
                "discovery_query": name,
                "discovery_source": getattr(exact[0], "source", ""),
            },
        })
    else:
        skipped.append({
            "name": name,
            "install_path": rel_install_path,
            "reason": "ambiguous" if len(exact) > 1 else "not_found",
        })

print(json.dumps({
    "discovered": discovered,
    "skipped": skipped,
}, ensure_ascii=False))
"""


def auto_discover_manual_public_skills(
    repo_root: Path,
    hermes_home: Path,
    manifest_path: Path,
    tracked_skills: list[TrackedSkill],
    manifest_root: dict[str, Any],
) -> tuple[list[TrackedSkill], dict[str, Any], list[str]]:
    proc = run_hermes_python(
        repo_root,
        hermes_home,
        _DISCOVER_MANUAL_SKILLS_SNIPPET,
        {
            "repo_root": str(repo_root),
            "hermes_home": str(hermes_home),
            "tracked_paths": [skill.install_path for skill in tracked_skills],
            "now_utc": _now_utc(),
        },
    )
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if proc.returncode != 0:
        raise RuntimeError(output or "Manual public skill discovery failed.")

    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        raise RuntimeError(output or "Manual public skill discovery produced invalid JSON.")

    discovered = payload.get("discovered", [])
    if not isinstance(discovered, list) or not discovered:
        return tracked_skills, manifest_root, []

    entries = list(manifest_root.get("skills", []))
    known_paths = {skill.install_path for skill in tracked_skills}
    known_identifiers = {skill.identifier for skill in tracked_skills}
    discovered_names: list[str] = []

    for entry in discovered:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("identifier", "")).strip()
        install_path = str(entry.get("install_path", "")).strip().strip("/")
        if not identifier or not install_path:
            continue
        if install_path in known_paths or identifier in known_identifiers:
            continue
        entries.append(entry)
        known_paths.add(install_path)
        known_identifiers.add(identifier)
        discovered_names.append(str(entry.get("name", "")).strip() or _derive_name_from_identifier(identifier))

    if not discovered_names:
        return tracked_skills, manifest_root, []

    manifest_root["skills"] = entries
    save_json(manifest_path, manifest_root)
    return (*load_tracked_skills(manifest_path), discovered_names)


_TRACKED_SKILL_CHECK_SNIPPET = r"""
import json
import os
import sys
from pathlib import Path


def load_simple_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


payload = json.loads(sys.argv[1])
repo_root = Path(payload["repo_root"]).resolve()
hermes_home = Path(payload["hermes_home"]).resolve()
os.environ["HERMES_HOME"] = str(hermes_home)
load_simple_env(hermes_home / ".env")
sys.path.insert(0, str(repo_root))

from tools.skills_guard import content_hash
from tools.skills_hub import GitHubAuth, bundle_content_hash, create_source_router

identifier = payload["identifier"]
install_path = payload["install_path"]
install_dir = hermes_home / "skills" / install_path
bundle = None
bundle_error = ""

for source in create_source_router(GitHubAuth()):
    try:
        bundle = source.fetch(identifier)
    except Exception as exc:
        bundle_error = str(exc)
        bundle = None
    if bundle:
        break

if not bundle:
    print(json.dumps({
        "ok": False,
        "status": "unavailable",
        "name": payload.get("name", ""),
        "install_path": install_path,
        "remote_hash": "",
        "local_hash": content_hash(install_dir) if install_dir.exists() else "",
        "output": bundle_error,
    }, ensure_ascii=False))
    raise SystemExit(0)

remote_hash = bundle_content_hash(bundle)
local_hash = content_hash(install_dir) if install_dir.exists() else ""
status = "update_available"
if local_hash and local_hash == remote_hash:
    status = "up_to_date"

print(json.dumps({
    "ok": True,
    "status": status,
    "name": bundle.name or payload.get("name", ""),
    "install_path": install_path,
    "remote_hash": remote_hash,
    "local_hash": local_hash,
    "output": "",
}, ensure_ascii=False))
"""


def check_tracked_skill(repo_root: Path, hermes_home: Path, skill: TrackedSkill) -> TrackedSkillCheck:
    proc = run_hermes_python(
        repo_root,
        hermes_home,
        _TRACKED_SKILL_CHECK_SNIPPET,
        {
            "repo_root": str(repo_root),
            "hermes_home": str(hermes_home),
            "identifier": skill.identifier,
            "name": skill.name,
            "install_path": skill.install_path,
        },
    )
    output = "\n".join(filter(None, [proc.stdout, proc.stderr])).strip()
    if proc.returncode != 0:
        return TrackedSkillCheck(
            ok=False,
            status="probe_failed",
            name=skill.name,
            install_path=skill.install_path,
            remote_hash="",
            local_hash="",
            output=output or "Tracked skill probe failed.",
        )

    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return TrackedSkillCheck(
            ok=False,
            status="probe_failed",
            name=skill.name,
            install_path=skill.install_path,
            remote_hash="",
            local_hash="",
            output=output or "Tracked skill probe produced invalid JSON.",
        )

    return TrackedSkillCheck(
        ok=bool(payload.get("ok", False)),
        status=str(payload.get("status", "")),
        name=str(payload.get("name", "")).strip() or skill.name,
        install_path=str(payload.get("install_path", "")).strip() or skill.install_path,
        remote_hash=str(payload.get("remote_hash", "")).strip(),
        local_hash=str(payload.get("local_hash", "")).strip(),
        output=str(payload.get("output", "")).strip(),
    )


def parse_manual_install_success(output: str) -> bool:
    return bool(_MANUAL_UPDATED_RE.search(output))


def run_tracked_skill_install(
    repo_root: Path,
    hermes_home: Path,
    skill: TrackedSkill,
) -> subprocess.CompletedProcess[str]:
    args = ["skills", "install", skill.identifier, "--force", "--yes"]
    if skill.category:
        args.extend(["--category", skill.category])
    return run_hermes_command(repo_root, hermes_home, *args)


def update_tracked_manual_skills(
    repo_root: Path,
    hermes_home: Path,
    manifest_path: Path,
) -> SkillUpdateStatus:
    tracked_skills, manifest_root = load_tracked_skills(manifest_path)
    if not tracked_skills:
        return SkillUpdateStatus(
            checked=False,
            updated_count=0,
            updated_names=[],
            output="",
        )

    updated_names: list[str] = []
    outputs: list[str] = []
    touched = False
    normalized_entries = manifest_root.get("skills", [])

    for index, skill in enumerate(tracked_skills):
        if not skill.enabled:
            continue

        touched = True
        check = check_tracked_skill(repo_root, hermes_home, skill)
        state = dict(skill.state)
        state["last_checked_at"] = _now_utc()

        if not check.ok and check.status != "unavailable":
            raise RuntimeError(
                f"Tracked skill probe failed for '{skill.name or skill.identifier}': {check.output or check.status}"
            )

        state["last_probe_status"] = check.status
        state["last_known_remote_hash"] = check.remote_hash
        state["last_known_local_hash"] = check.local_hash
        if check.name:
            normalized_entries[index]["name"] = check.name
        normalized_entries[index]["install_path"] = check.install_path
        normalized_entries[index]["state"] = state

        if check.status == "unavailable":
            raise RuntimeError(
                f"Tracked skill unavailable for '{skill.name or skill.identifier}': {check.output or 'fetch failed'}"
            )
        if check.status == "up_to_date":
            continue

        install_proc = run_tracked_skill_install(repo_root, hermes_home, skill)
        install_output = "\n".join(filter(None, [install_proc.stdout, install_proc.stderr]))
        outputs.append(install_output.strip())
        if install_proc.returncode != 0 or not parse_manual_install_success(install_output):
            raise RuntimeError(
                f"Tracked skill update failed for '{skill.name or skill.identifier}': "
                f"{tail_lines(install_output or 'install command failed')}"
            )

        updated_name = check.name or skill.name or _derive_name_from_identifier(skill.identifier)
        updated_names.append(updated_name)
        state["last_updated_at"] = _now_utc()
        state["last_probe_status"] = "updated"
        state["last_applied_hash"] = check.remote_hash
        normalized_entries[index]["state"] = state

    if touched:
        manifest_root["skills"] = normalized_entries
        save_json(manifest_path, manifest_root)

    return SkillUpdateStatus(
        checked=touched,
        updated_count=len(updated_names),
        updated_names=updated_names,
        output="\n\n".join(text for text in outputs if text),
    )


def format_success(
    status_before: UpdateStatus,
    status_after: UpdateStatus,
    profile_name: str,
    skill_status: SkillUpdateStatus,
) -> str:
    lines = [
        "Hermes 일일 자동 업데이트 결과",
        "상태: 업데이트 성공",
        f"프로필: {profile_name}",
        f"호스트: {socket.gethostname()}",
        f"시각(UTC): {_now_utc()}",
        "저장소: NousResearch/hermes-agent",
        f"적용 커밋 수: {status_before.behind_count}",
        f"적용 공개 스킬 수: {skill_status.updated_count if skill_status.checked else 0}",
        f"HEAD: {_short_sha(status_before.local_head)} -> {_short_sha(status_after.local_head)}",
    ]
    if status_before.commit_lines:
        lines.append("")
        lines.append("반영된 커밋:")
        lines.extend(f"- {line}" for line in status_before.commit_lines)
    if skill_status.updated_names:
        lines.append("")
        lines.append("반영된 공개 스킬:")
        lines.extend(f"- {name}" for name in skill_status.updated_names[:10])
    return "\n".join(lines)


def format_update_failure(
    status_before: UpdateStatus,
    profile_name: str,
    output: str,
    *,
    failure_stage: str,
) -> str:
    return "\n".join(
        [
            "Hermes 일일 자동 업데이트 결과",
            "상태: 업데이트 실패",
            f"프로필: {profile_name}",
            f"호스트: {socket.gethostname()}",
            f"시각(UTC): {_now_utc()}",
            "저장소: NousResearch/hermes-agent",
            f"실패 단계: {failure_stage}",
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


def format_update_deferred(
    profile_name: str,
    blockers: list[GatewayActivityStatus],
    *,
    session_window_seconds: int,
) -> str:
    lines = [
        "Hermes 일일 자동 업데이트 결과",
        "상태: 업데이트 보류",
        f"프로필: {profile_name}",
        f"호스트: {socket.gethostname()}",
        f"시각(UTC): {_now_utc()}",
        f"보류 기준: 최근 {session_window_seconds}초 내 gateway 활동",
        "",
        "감지된 활동:",
    ]
    for status in blockers:
        details: list[str] = [f"profile={status.profile_name}", f"pid={status.pid}"]
        if status.blocker_reasons:
            details.append(f"reasons={','.join(status.blocker_reasons)}")
        if status.active_agents > 0:
            details.append(f"active_agents={status.active_agents}")
        if status.recent_session_count > 0:
            details.append(f"recent_sessions={status.recent_session_count}")
        if status.recently_updated_platforms:
            details.append(f"recent_platforms={','.join(status.recently_updated_platforms)}")
        if status.restart_requested:
            details.append("restart_requested=true")
        if status.gateway_state:
            details.append(f"state={status.gateway_state}")
        if status.runtime_updated_at:
            details.append(f"runtime_updated_at={status.runtime_updated_at}")
        if status.recent_session_updated_at:
            details.append(f"recent_session_at={status.recent_session_updated_at}")
        lines.append(f"- {', '.join(details)}")
    return "\n".join(lines)


def format_worktree_deferred(profile_name: str, worktree_status: WorktreeStatus) -> str:
    lines = [
        "Hermes 일일 자동 업데이트 결과",
        "상태: 업데이트 보류",
        f"프로필: {profile_name}",
        f"호스트: {socket.gethostname()}",
        f"시각(UTC): {_now_utc()}",
        "보류 기준: hermes-agent 워크트리에 추적된 로컬 변경이 있음",
        "",
        "감지된 변경:",
    ]
    lines.extend(f"- {line}" for line in worktree_status.lines[:10])
    if len(worktree_status.lines) > 10:
        lines.append(f"- ... 외 {len(worktree_status.lines) - 10}개")
    return "\n".join(lines)


def format_no_update(profile_name: str, status: UpdateStatus, *, skill_updates_checked: bool) -> str:
    lines = [
        "Hermes 일일 자동 업데이트 결과",
        "상태: 업데이트 없음",
        f"프로필: {profile_name}",
        f"호스트: {socket.gethostname()}",
        f"시각(UTC): {_now_utc()}",
        f"현재 HEAD: {_short_sha(status.local_head)}",
    ]
    if skill_updates_checked:
        lines.append("공개 스킬 상태: 업데이트 없음")
    return "\n".join(lines)


def run_once(config: dict[str, Any]) -> int:
    repo_root = Path(config["repo_root"]).expanduser().resolve()
    hermes_home = Path(config["hermes_home"]).expanduser().resolve()
    channel_id = str(config.get("discord_channel_id", DEFAULT_CHANNEL)).strip()
    remote = str(config.get("remote", DEFAULT_REMOTE))
    branch = str(config.get("branch", DEFAULT_BRANCH))
    notify_on_no_update = bool(config.get("notify_on_no_update", False))
    defer_if_recent_gateway_activity = bool(
        config.get(
            "defer_if_recent_gateway_activity",
            DEFAULT_DEFER_IF_RECENT_GATEWAY_ACTIVITY,
        )
    )
    defer_if_repo_dirty = bool(config.get("defer_if_repo_dirty", DEFAULT_DEFER_IF_REPO_DIRTY))
    recent_session_window_seconds = max(
        0,
        _safe_int(
            config.get(
                "recent_session_window_seconds",
                DEFAULT_RECENT_SESSION_WINDOW_SECONDS,
            )
            or 0
        ),
    )
    auto_update_official_skills = resolve_update_toggle(
        config,
        "auto_update_official_skills",
        default=DEFAULT_AUTO_UPDATE_OFFICIAL_SKILLS,
    )
    auto_update_custom_skills = resolve_update_toggle(
        config,
        "auto_update_custom_skills",
        default=DEFAULT_AUTO_UPDATE_CUSTOM_SKILLS,
    )
    enable_auto_discover_manual_public_skills = bool(
        config.get("auto_discover_manual_public_skills", DEFAULT_AUTO_DISCOVER_MANUAL_PUBLIC_SKILLS)
    )
    config_path = Path(config.get("_config_path", "")).expanduser().resolve() if config.get("_config_path") else None
    manual_skills_manifest = Path(
        config.get(
            "tracked_public_skills_manifest",
            str(default_manual_skills_manifest(config_path)) if config_path else DEFAULT_MANUAL_SKILLS_MANIFEST,
        )
    ).expanduser().resolve()
    profile_name = hermes_home.name if hermes_home.name != ".hermes" else "default"

    env_values = load_simple_env(hermes_home / ".env")
    discord_token = env_values.get("DISCORD_BOT_TOKEN", "").strip()

    if not channel_id:
        print("discord_channel_id is missing in config.json", file=sys.stderr)
        return 2

    if not discord_token:
        print("DISCORD_BOT_TOKEN is missing in the Hermes profile .env", file=sys.stderr)
        return 2

    if defer_if_repo_dirty:
        try:
            worktree_status = collect_worktree_status(repo_root)
        except subprocess.CalledProcessError as exc:
            output = "\n".join(filter(None, [exc.stdout, exc.stderr])) or str(exc)
            send_discord_message(channel_id, discord_token, format_check_failure(profile_name, output))
            print(output, file=sys.stderr)
            return 1
        if worktree_status.dirty:
            send_discord_message(
                channel_id,
                discord_token,
                format_worktree_deferred(profile_name, worktree_status),
            )
            print(
                "Deferred update because hermes-agent has tracked local changes: "
                + "; ".join(worktree_status.lines[:5])
            )
            return 0

    if defer_if_recent_gateway_activity and recent_session_window_seconds > 0:
        blockers = detect_recent_gateway_activity(
            hermes_home,
            session_window_seconds=recent_session_window_seconds,
        )
        if blockers:
            message = format_update_deferred(
                profile_name,
                blockers,
                session_window_seconds=recent_session_window_seconds,
            )
            send_discord_message(channel_id, discord_token, message)
            print(
                "Deferred update due to recent gateway activity: "
                + ", ".join(
                    f"{status.profile_name}(agents={status.active_agents}, sessions={status.recent_session_count})"
                    for status in blockers
                )
            )
            return 0

    try:
        status_before = collect_update_status(repo_root, remote=remote, branch=branch)
    except subprocess.CalledProcessError as exc:
        output = "\n".join(filter(None, [exc.stdout, exc.stderr])) or str(exc)
        send_discord_message(channel_id, discord_token, format_check_failure(profile_name, output))
        print(output, file=sys.stderr)
        return 1

    repo_updated = False
    status_after = status_before

    if status_before.behind_count > 0:
        update_proc = run_update(repo_root, hermes_home)
        update_output = "\n".join(filter(None, [update_proc.stdout, update_proc.stderr]))
        if update_proc.returncode != 0:
            send_discord_message(
                channel_id,
                discord_token,
                format_update_failure(
                    status_before,
                    profile_name,
                    update_output,
                    failure_stage="Hermes 코드 업데이트",
                ),
            )
            print(update_output or "Update failed.", file=sys.stderr)
            return update_proc.returncode or 1

        status_after = collect_update_status(repo_root, remote=remote, branch=branch)
        repo_updated = True

    official_skill_status = SkillUpdateStatus(
        checked=False,
        updated_count=0,
        updated_names=[],
        output="",
    )
    if auto_update_official_skills:
        try:
            official_skill_status = update_hub_skills(
                repo_root,
                hermes_home,
                mode="official",
            )
        except Exception as exc:
            send_discord_message(
                channel_id,
                discord_token,
                format_update_failure(
                    status_after,
                    profile_name,
                    str(exc),
                    failure_stage="Hermes 공식 스킬 업데이트",
                ),
            )
            print(str(exc), file=sys.stderr)
            return 1

    custom_hub_skill_status = SkillUpdateStatus(
        checked=False,
        updated_count=0,
        updated_names=[],
        output="",
    )
    manual_skill_status = SkillUpdateStatus(
        checked=False,
        updated_count=0,
        updated_names=[],
        output="",
    )
    if auto_update_custom_skills:
        try:
            custom_hub_skill_status = update_hub_skills(
                repo_root,
                hermes_home,
                mode="custom",
            )
            tracked_skills, manifest_root = load_tracked_skills(manual_skills_manifest)
            if enable_auto_discover_manual_public_skills:
                tracked_skills, manifest_root, discovered_names = auto_discover_manual_public_skills(
                    repo_root,
                    hermes_home,
                    manual_skills_manifest,
                    tracked_skills,
                    manifest_root,
                )
                if discovered_names:
                    print(
                        f"Auto-discovered {len(discovered_names)} manual public skill(s): "
                        f"{', '.join(discovered_names)}"
                    )
            manual_skill_status = update_tracked_manual_skills(
                repo_root,
                hermes_home,
                manual_skills_manifest,
            )
        except Exception as exc:
            send_discord_message(
                channel_id,
                discord_token,
                format_update_failure(
                    status_after,
                    profile_name,
                    str(exc),
                    failure_stage="커스텀 스킬 업데이트",
                ),
            )
            print(str(exc), file=sys.stderr)
            return 1

    combined_skill_status = combine_skill_statuses(
        official_skill_status,
        custom_hub_skill_status,
        manual_skill_status,
    )

    if not repo_updated and combined_skill_status.updated_count == 0:
        if notify_on_no_update:
            send_discord_message(
                channel_id,
                discord_token,
                format_no_update(
                    profile_name,
                    status_before,
                    skill_updates_checked=combined_skill_status.checked,
                ),
            )
        print("No updates available.")
        return 0

    send_discord_message(
        channel_id,
        discord_token,
        format_success(status_before, status_after, profile_name, combined_skill_status),
    )
    summary_parts = []
    if repo_updated:
        summary_parts.append(
            f"repo {_short_sha(status_before.local_head)} -> {_short_sha(status_after.local_head)} "
            f"({status_before.behind_count} commits)"
        )
    if combined_skill_status.updated_count:
        summary_parts.append(f"{combined_skill_status.updated_count} public skill(s)")
    print("Updated " + ", ".join(summary_parts))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    config["_config_path"] = str(args.config.expanduser().resolve())
    lock_path = build_lock_path(config)

    try:
        lock_file = acquire_run_lock(lock_path)
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 0

    try:
        return run_once(config)
    finally:
        release_run_lock(lock_file)


if __name__ == "__main__":
    raise SystemExit(main())
