# hermes update auto

Standalone daily updater for a local `hermes-agent` git checkout.

What it does:

- checks `origin/main` once a day
- does nothing when there is no new commit
- runs `hermes update` automatically when updates exist
- sends a Korean status message to a Discord channel

It is intentionally external to Hermes scheduling. Self-updating is safer from
`launchd` or Windows Task Scheduler than from an in-process Hermes cron job,
because `hermes update` can restart services during the run.

## Requirements

- a local `hermes-agent` git checkout
- a working Hermes profile with `.env`
- `DISCORD_BOT_TOKEN` present in that Hermes profile `.env`
- your Discord bot must be in the server and able to post to the target channel

## Config

Copy `config.example.json` to `config.json` and edit:

```json
{
  "repo_root": "/absolute/path/to/hermes-agent",
  "hermes_home": "/absolute/path/to/.hermes/profiles/main",
  "discord_channel_id": "1491641510867763200",
  "remote": "origin",
  "branch": "main",
  "notify_on_no_update": false
}
```

## Run Once

macOS / Linux:

```bash
python3 hermes_update_auto.py --config config.json
```

Windows:

```powershell
py -3 .\hermes_update_auto.py --config .\config.json
```

## macOS launchd

Install a daily job for `09:00`:

```bash
zsh install_macos_launchd.sh 9 0
```

This writes:

- `~/Library/LaunchAgents/ai.hermes.daily-repo-update.plist`
- `daily-update.stdout.log`
- `daily-update.stderr.log`

## Windows Task Scheduler

Install a daily task for `09:00`:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -Time 09:00
```

This creates a scheduled task named `HermesDailyRepoUpdate`.

## Discord message format

The updater sends Korean status values:

- `상태: 업데이트 성공`
- `상태: 업데이트 실패`
- `상태: 업데이트 확인 실패`

If `notify_on_no_update` is `true`, it also sends:

- `상태: 업데이트 없음`
