<div align="center">

![Hermes Auto Update](docs/readme-banner.svg)

# Hermes Auto Update

Keep a local Hermes checkout current while preserving registered customizations.

**English** · [한국어](README.ko.md)

![project: automation](https://img.shields.io/badge/project-automation-4ade80?style=flat-square) ![stack: Python](https://img.shields.io/badge/stack-Python-4ade80?style=flat-square) [![License](https://img.shields.io/badge/license-MIT-4ade80?style=flat-square)](LICENSE)

[Configure](#configure) · [Run once](#run-once) · [Safeguards](#preserve-local-overlays)

</div>

---

## Overview

An external updater for an existing `hermes-agent` checkout. It checks upstream changes, invokes `hermes update` when appropriate, updates selected public skills and reports the result to a Discord channel in Korean.

It is intended to run through an external scheduler such as macOS launchd or Windows Task Scheduler. `hermes update` can restart services, so the updater should not depend on Hermes's own cron lifecycle.

## What it handles

- Separate switches for official and custom skill updates.
- Discovery of manually installed public skills when exactly one upstream name match exists.
- A manifest for explicitly tracked public skills.
- Deferral when unregistered local changes, local commits or recent gateway activity make updating unsafe.
- Snapshot, stash, reapplication and tests for registered local overlays.
- Optional local-commit replay with backup branches and patch files.
- Rollback and an optional one-time Codex conflict handoff.
- Duplicate-run protection for the same repository/profile.

## Requirements

- An existing local `hermes-agent` repository and working Hermes profile.
- A profile `.env` containing `DISCORD_BOT_TOKEN`.
- A bot that belongs to your Discord server and can post to the selected channel.
- Python available in the scheduler's execution environment.

Keep actual configuration, profile secrets, account paths and channel IDs in local files. The repository provides generalized examples.

## Configure

```bash
git clone https://github.com/wilgon456/Hermes_Auto_Update.git
cd Hermes_Auto_Update
cp config.example.json config.json
```

On Windows, use `Copy-Item config.example.json config.json`. Edit the local configuration to point to your checkout, Hermes profile and Discord channel.

<details>
<summary>macOS / Linux configuration example</summary>

```json
{
  "repo_root": "/absolute/path/to/hermes-agent",
  "hermes_home": "/absolute/path/to/.hermes/profiles/main",
  "discord_channel_id": "YOUR_DISCORD_CHANNEL_ID",
  "remote": "origin",
  "branch": "main",
  "auto_update_official_skills": true,
  "auto_update_custom_skills": true,
  "auto_discover_manual_public_skills": true,
  "defer_if_repo_dirty": true,
  "ignored_worktree_paths": [],
  "ignored_untracked_paths": [
    "plans/**"
  ],
  "defer_if_local_commits": true,
  "defer_if_recent_gateway_activity": true,
  "recent_session_window_seconds": 120,
  "update_timeout_seconds": 3600,
  "tracked_public_skills_manifest": "/absolute/path/to/tracked-public-skills.json",
  "notify_on_no_update": false,
  "local_commit_backup_dir": "/absolute/path/to/.hermes/profiles/main/backups/hermes-update-local-commits",
  "local_commit_backup_keep": 10,
  "codex_conflict_handoff": {
    "enabled": false,
    "command": "codex",
    "timeout_seconds": 7200,
    "sandbox": "workspace-write",
    "approval": "never",
    "model": "",
    "extra_args": [],
    "log_dir": "/absolute/path/to/.hermes/profiles/main/backups/hermes-update-codex-handoff"
  },
  "local_overlay_snapshot_dir": "/absolute/path/to/.hermes/profiles/main/backups/hermes-update-overlays",
  "local_overlays": [
    {
      "id": "my-local-runtime-patch",
      "description": "Preserve a local-only runtime patch across upstream updates.",
      "paths": [
        "gateway/platforms/example.py",
        "gateway/run.py",
        "tests/gateway/test_example_runtime_patch.py"
      ],
      "tests": [
        "venv/bin/python -m pytest -o addopts='' tests/gateway/test_example_runtime_patch.py -q"
      ],
      "required": true
    }
  ]
}
```

</details>

<details>
<summary>Windows configuration example</summary>

```json
{
  "repo_root": "C:\\Users\\you\\hermes-agent",
  "hermes_home": "C:\\Users\\you\\.hermes\\profiles\\main",
  "discord_channel_id": "YOUR_DISCORD_CHANNEL_ID",
  "remote": "origin",
  "branch": "main",
  "auto_update_official_skills": true,
  "auto_update_custom_skills": true,
  "auto_discover_manual_public_skills": true,
  "defer_if_repo_dirty": true,
  "ignored_worktree_paths": [],
  "ignored_untracked_paths": [
    "plans/**"
  ],
  "defer_if_local_commits": true,
  "defer_if_recent_gateway_activity": true,
  "recent_session_window_seconds": 120,
  "update_timeout_seconds": 3600,
  "tracked_public_skills_manifest": "C:\\Users\\you\\hermes-update-auto\\tracked-public-skills.json",
  "notify_on_no_update": false,
  "local_commit_backup_dir": "C:\\Users\\you\\.hermes\\profiles\\main\\backups\\hermes-update-local-commits",
  "local_commit_backup_keep": 10,
  "codex_conflict_handoff": {
    "enabled": false,
    "command": "codex",
    "timeout_seconds": 7200,
    "sandbox": "workspace-write",
    "approval": "never",
    "model": "",
    "extra_args": [],
    "log_dir": "C:\\Users\\you\\.hermes\\profiles\\main\\backups\\hermes-update-codex-handoff"
  },
  "local_overlay_snapshot_dir": "C:\\Users\\you\\.hermes\\profiles\\main\\backups\\hermes-update-overlays",
  "local_overlays": [
    {
      "id": "my-local-runtime-patch",
      "description": "Preserve a local-only runtime patch across upstream updates.",
      "paths": [
        "gateway/platforms/example.py",
        "gateway/run.py",
        "tests/gateway/test_example_runtime_patch.py"
      ],
      "tests": [
        "venv\\Scripts\\python.exe -m pytest -o addopts='' tests/gateway/test_example_runtime_patch.py -q"
      ],
      "required": true
    }
  ]
}
```

</details>

## Run once

macOS / Linux:

```bash
python3 hermes_update_auto.py --config config.json
```

Windows:

```powershell
py -3 .\hermes_update_auto.py --config .\config.json
```

This is a real updater invocation, not a preview. Review the configuration and gateway state first.

## Schedule

| Platform | Install command | Result |
| --- | --- | --- |
| macOS | `zsh install_macos_launchd.sh 9 0` | Daily 09:00 LaunchAgent |
| Windows | `powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -Time 09:00` | `HermesDailyRepoUpdate` task |
| Linux | Configure cron or a systemd timer yourself | No dedicated installer is provided |

The macOS installer creates `~/Library/LaunchAgents/ai.hermes.daily-repo-update.plist` and stdout/stderr log files.

## Important settings

| Setting | Behavior |
| --- | --- |
| `auto_update_official_skills` | Updates hub-installed optional skills whose source is `official` |
| `auto_update_custom_skills` | Updates non-official hub skills and manually tracked public skills |
| `auto_discover_manual_public_skills` | Registers an untracked manual skill only when the exact upstream name match is unique |
| `tracked_public_skills_manifest` | Manifest path; defaults next to `config.json` |
| `defer_if_repo_dirty` | Defaults to `true`; defers unless every dirty path belongs to a registered overlay |
| `ignored_worktree_paths` | Relative path patterns ignored for tracked and untracked dirtiness |
| `ignored_untracked_paths` | Ignore patterns applied only to untracked files |
| `defer_if_local_commits` | Defaults to `true`; `false` enables backed-up local-commit replay |
| `defer_if_recent_gateway_activity` | Defaults to `true`; inspects gateway/platform/session state |
| `recent_session_window_seconds` | Defaults to 120 seconds |
| `update_timeout_seconds` | Defaults to 3600 seconds; timed-out updater subprocess groups are terminated |
| `notify_on_no_update` | Sends a no-update Discord message when enabled |

## Track public skills

The manifest can be populated automatically or edited explicitly:

```json
{
  "version": 1,
  "skills": [
    {
      "identifier": "skills-sh/example/repo/k-skill",
      "name": "k-skill",
      "category": "community",
      "enabled": true
    }
  ]
}
```

Each entry requires `identifier`; `name`, `category`, `install_path` and `enabled` are optional. `install_path` overrides `category/name`, and `enabled` defaults to `true`.

Each run compares the upstream bundle hash with the installed contents. Changed skills are reinstalled with `hermes skills install <identifier> --force --yes`, and the manifest's `state` block records the last check, applied hash and probe result.

Automatic discovery excludes hub-installed skills, bundled skills, already tracked skills and ambiguous upstream matches.

## Preserve local overlays

Register small local runtime patches in `local_overlays` using `id`, `description`, relative `paths`, public-safe `tests` and `required`.

1. Inspect all tracked and untracked changes.
2. Defer if any changed path falls outside the registered overlays.
3. Save a snapshot containing status, staged/unstaged diffs, untracked files and the overlay manifest.
4. Stash only the overlay paths.
5. Run `hermes update`.
6. Reapply the recorded stash **object SHA**, rather than a possibly shifted stash index.
7. Run overlay tests and report the result.

Snapshots are stored under `local_overlay_snapshot_dir`. Overlays preserve code changes; they are not a way to publish private configuration or secrets. The updater does not perform a separate gateway restart after reapplying an overlay; use your own operating procedure if one is required.

## Local commits and conflict recovery

When `defer_if_local_commits` is disabled, the updater creates a `backup/hermes-auto-update-<timestamp>` branch and `format-patch` backup before resetting the checkout to upstream and replaying local commits with `git am --3way`. A conflict aborts the replay and rolls back to the original HEAD, reporting conflict files and backup locations. Actual local merge commits defer rather than attempt an unsafe patch replay.

`local_commit_backup_dir` selects the backup location; `local_commit_backup_keep` defaults to 10. The default location is under the selected Hermes profile's `backups/hermes-update-local-commits` directory.

`codex_conflict_handoff.enabled` defaults to `false`. If enabled, a one-time headless `codex exec` runs **after rollback**. It can edit the local checkout. Prompt, output and last-message files are saved under the configured log directory. Use it only in a machine/profile you control.

## Gateway and notification boundaries

`hermes update` may restart the gateway or stop a manually started gateway. Recent agents, sessions, connected platform activity and starting/draining/stopping/restarting signals can defer the update. These checks reduce interruption risk; they do not make an update free of service lifecycle effects.

Discord messages distinguish success, failure, check failure, deferral and optionally no update. Success messages report applied repository commits and updated public skills, including hub updates and tracked manual skills.

## Files

| File | Purpose |
| --- | --- |
| `hermes_update_auto.py` | Update checks, preservation and reporting |
| `config.example.json` | Generalized configuration |
| `tracked-public-skills.example.json` | Public skill tracking example |
| `install_macos_launchd.sh` | macOS scheduler installer |
| `install_windows_task.ps1` | Windows scheduler installer |

## License

[MIT](LICENSE).
