# Hermes Auto Update

공식 `hermes-agent` 저장소를 직접 수정하지 않고, 로컬 체크아웃을 매일 점검해서 안전하게 업데이트하는 외부 업데이터입니다. 개인 커스텀을 유지하면서 upstream 변경을 따라가야 하는 환경을 목표로 합니다.

## 주요 기능

- 매일 `origin/main`을 확인합니다.
- 저장소 업데이트가 있으면 `hermes update`를 실행합니다.
- Hermes 공식 스킬 업데이트와 커스텀 스킬 업데이트를 각각 on/off 할 수 있습니다.
- `~/.hermes/skills` 아래의 수동 설치 스킬 중, 공개 소스에서 정확히 하나로 식별되는 경우 자동으로 추적 목록에 등록합니다.
- 별도 manifest에 등록된 수동 설치 공개 스킬도 upstream 내용을 다시 확인해서 자동 갱신합니다.
- 같은 repo/profile 조합에 대해 중복 실행이 감지되면 새 실행은 바로 종료합니다.
- `hermes-agent` 워크트리에 로컬 변경이 있으면 기본적으로 업데이트를 보류합니다.
- 등록된 `local_overlays`에 해당하는 로컬 커스텀 패치는 업데이트 전 snapshot/stash 후 자동 재적용하고 테스트합니다.
- `defer_if_local_commits: false`인 경우 upstream과 로컬 커밋이 갈라져도 백업 브랜치와 patch 백업을 만든 뒤 upstream 위에 로컬 커밋을 `git am --3way`로 재적용합니다.
- 로컬 커밋 재적용 중 충돌이 나면 원래 HEAD로 rollback하고, 충돌 파일/백업 브랜치/패치 백업 위치를 보고합니다.
- `codex_conflict_handoff.enabled`가 켜져 있으면 충돌 rollback 후 `codex exec`를 한 번 호출해서 복구 작업을 이어받게 하고, handoff prompt/output/last-message를 파일로 남깁니다.
- gateway가 최근에 활동 중인 것으로 보이면 업데이트를 보류합니다.
- 저장소와 공개 스킬 모두 변경이 없으면 아무 작업도 하지 않습니다.
- 결과를 Discord 채널에 한국어 메시지로 전송합니다.

이 업데이터는 Hermes 내부 cron이 아니라 별도 스케줄러에서 돌리도록 설계했습니다. `hermes update` 실행 중 서비스 재시작이 일어날 수 있어서, `launchd`나 Windows Task Scheduler 같은 외부 스케줄러에서 돌리는 편이 더 안전합니다.

## 요구 사항

- 로컬에 `hermes-agent` 저장소가 있어야 합니다.
- 정상 동작하는 Hermes 프로필과 `.env`가 있어야 합니다.
- 해당 프로필 `.env`에 `DISCORD_BOT_TOKEN`이 있어야 합니다.
- Discord 봇이 대상 서버에 들어가 있고, 대상 채널에 메시지를 쓸 수 있어야 합니다.

## 지원 환경

- macOS: `launchd` 설치 스크립트 제공
- Windows: Task Scheduler 설치 스크립트 제공
- Linux: 실행 자체는 가능하지만 전용 설치 스크립트는 아직 없으므로 cron/systemd timer는 수동 구성 기준입니다.

## 안전 주의사항

- 이 도구는 `hermes update`를 호출하므로, 실제 업데이트 시 Hermes gateway 재시작이나 manual gateway 종료가 일어날 수 있습니다.
- 현재 기본 동작은 로컬 변경이나 최근 gateway 활동이 감지되면 업데이트를 보류하는 것입니다. 단, 등록된 `local_overlays`만 변경된 경우에는 자동 보존/재적용을 시도합니다.
- gateway 보류 판단은 `gateway_state.json`, 플랫폼 상태, `sessions/sessions.json` 기준입니다.
- `hermes-agent`에 미등록 로컬 변경이 있으면 충돌을 피하기 위해 `hermes update`를 호출하지 않습니다.
- 이 저장소에는 실제 `config.json`, 프로필 `.env`, Discord 토큰, 채널 ID 같은 비밀값을 포함하면 안 됩니다.
- `config.example.json`에는 일반화된 예시만 두고, 개인 경로/계정/채널 ID/토큰은 각자의 로컬 `config.json`에만 보관하세요.

## 설정

`config.example.json`을 `config.json`으로 복사한 뒤 수정합니다.

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

Windows 예시는 다음과 같습니다.

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

설정 항목 설명:

- `auto_update_official_skills`
  Hermes 공식 스킬 자동 업데이트 여부입니다. 기본값은 `true`입니다.
  source가 `official`인 hub-installed 공식 optional skill만 업데이트합니다.
- `auto_update_custom_skills`
  커스텀 스킬 자동 업데이트 여부입니다. 기본값은 `true`입니다.
  source가 `official`이 아닌 hub-installed 스킬과 `tracked-public-skills.json`에 들어있는 수동 추적 스킬을 업데이트합니다.
- `auto_discover_manual_public_skills`
  수동 설치 공개 스킬 자동 발견 여부입니다. 기본값은 `true`입니다.
  `auto_update_custom_skills`가 켜져 있을 때만 동작합니다.
  `~/.hermes/skills`를 스캔해서 hub-installed 스킬과 bundled 스킬을 제외하고, upstream 검색 결과가 exact-name으로 정확히 1개일 때만 자동 등록합니다.
- `tracked_public_skills_manifest`
  수동 설치 공개 스킬 추적 파일 경로입니다. 생략하면 `config.json` 옆의 `tracked-public-skills.json`을 사용합니다.
- `defer_if_repo_dirty`
  기본값은 `true`입니다.
  `hermes-agent` 워크트리에 로컬 변경이 있으면 업데이트를 보류합니다.
  단, 변경된 경로가 모두 `local_overlays`에 등록되어 있으면 업데이트 전 snapshot과 stash를 만들고, 업데이트 후 해당 overlay를 재적용한 뒤 overlay별 테스트를 실행합니다.
  운영 환경에서는 `true`를 권장합니다. `false`로 끄면 `hermes update`의 stash/restore 충돌 시 로컬 패치가 적용되지 않은 상태로 gateway가 다시 실행될 수 있습니다.
- `ignored_worktree_paths`, `ignored_untracked_paths`
  dirty check에서 무시할 repo 상대 경로 패턴입니다. `plans/**`처럼 자동 업데이트와 무관한 산출물 디렉터리를 넣을 수 있습니다.
  `ignored_worktree_paths`는 tracked/untracked 모두에 적용되고, `ignored_untracked_paths`는 `??` untracked 항목에만 적용됩니다.
- `local_overlay_snapshot_dir`
  등록된 local overlay를 업데이트 전에 보존할 snapshot 디렉터리입니다.
  snapshot에는 `git status`, tracked/staged diff, untracked 파일 복사본, overlay manifest가 들어갑니다.
- `local_overlays`
  업데이트 때 자동으로 보존/재적용할 로컬 커스텀 패치 목록입니다.
  각 overlay는 `id`, `description`, `paths`, `tests`, `required`를 가질 수 있습니다.
  dirty path가 등록된 overlay의 `paths` 밖에 하나라도 있으면 updater는 안전하게 보류합니다.
  stash restore는 기존 stash index가 아니라 생성된 stash object SHA를 기준으로 apply/drop하므로, 기존 사용자 stash를 잘못 pop하지 않습니다.
- `defer_if_local_commits`
  기본값은 `true`입니다.
  현재 브랜치에 upstream에 없는 로컬 커밋이 있으면 업데이트를 보류합니다.
  `false`로 설정하면 upstream 업데이트가 있을 때 다음 순서로 로컬 커밋을 자동 재적용합니다.
  1. `backup/hermes-auto-update-<timestamp>` 백업 브랜치를 만듭니다.
  2. `local_commit_backup_dir` 아래에 `format-patch` 결과를 저장합니다.
  3. checkout을 `origin/main`으로 `reset --hard`합니다.
  4. 저장된 patch를 `git am --3way`로 하나씩 적용합니다.
  5. 충돌이 나면 `git am --abort` 후 백업 브랜치로 rollback하고 충돌 파일을 보고합니다.
  upstream에서 이미 온 merge commit은 안전하게 제외하고, 실제 로컬 merge commit은 `format-patch`로 의도를 보존하기 어렵기 때문에 자동 재적용하지 않고 보류합니다.
- `local_commit_backup_dir`, `local_commit_backup_keep`
  로컬 커밋 자동 재적용 전 생성하는 patch 백업 디렉터리와 보존 개수입니다.
  기본 위치는 `~/.hermes/profiles/<profile>/backups/hermes-update-local-commits`이며 기본 보존 개수는 10입니다.
- `codex_conflict_handoff`
  로컬 커밋 자동 재적용 충돌 후 Codex CLI에게 복구를 넘기는 선택 기능입니다.
  `enabled: true`이면 updater가 먼저 안전 rollback을 끝낸 뒤 `codex exec`를 호출합니다.
  기본 명령은 `codex exec --cd <repo> --add-dir <patch-backup-dir> --sandbox workspace-write --ask-for-approval never` 형태입니다.
  결과는 `log_dir/<timestamp>/prompt.md`, `codex-output.log`, `codex-last-message.md`에 저장됩니다.
  일반 배포 예시는 `false`를 권장합니다. headless Codex가 로컬 repo를 수정할 수 있으므로, 본인이 사용하는 단일 머신/프로필에서만 켜세요.
- `defer_if_recent_gateway_activity`
  기본값은 `true`입니다.
  Hermes gateway 상태 파일, platform update timestamp, session index를 보고 최근 활동이 감지되면 이번 실행의 업데이트를 보류합니다.
  `active_agents`, 최근 session, 최근 connected platform activity, `restart_requested`, `starting`/`draining`/`stopping`/`restarting` 상태를 보류 신호로 봅니다.
- `recent_session_window_seconds`
  기본값은 `120`초입니다.
  최근 session activity로 간주할 시간 창입니다.
- `update_timeout_seconds`
  기본값은 `3600`초입니다.
  `hermes update`가 이 시간을 넘기면 하위 프로세스 그룹을 종료하고 실패로 보고합니다.
- `notify_on_no_update`
  변경이 없어도 Discord에 `상태: 업데이트 없음` 메시지를 보낼지 결정합니다.

## 수동 설치 공개 스킬 추적

업데이터는 이 manifest를 자동으로 채울 수 있지만, identifier를 이미 알고 있다면 직접 넣어도 됩니다.

예시 manifest:

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

각 항목에서 지원하는 필드:

- `identifier`: 필수, 공개 스킬 identifier
- `name`: 선택, 표시용 이름
- `category`: 선택, `~/.hermes/skills` 아래 설치 카테고리
- `install_path`: 선택, 상대 설치 경로. 지정하면 `category/name`보다 우선합니다.
- `enabled`: 선택, 기본값은 `true`

매 실행마다 이 manifest에 등록된 스킬의 upstream bundle을 다시 확인합니다. 원격 hash와 현재 로컬 설치본 hash가 다르면 아래 명령으로 다시 설치합니다.

```bash
hermes skills install <identifier> --force --yes
```

그리고 manifest의 `state` 블록에 마지막 확인 시각, 마지막 적용 hash, probe 상태 등을 기록합니다.

## Managed local overlays

개인 또는 팀 환경에서 upstream에는 아직 없는 작은 런타임 패치를 유지해야 할 때 `local_overlays`를 사용합니다.

동작 순서:

1. `git status --porcelain --untracked-files=all`로 로컬 변경을 확인합니다.
2. 변경된 모든 경로가 등록된 overlay의 `paths`에 포함되는지 확인합니다.
3. 미등록 경로가 있으면 업데이트를 보류합니다.
4. 등록된 overlay만 있으면 snapshot을 저장합니다.
5. overlay 경로만 `git stash push -u`로 임시 보존합니다.
6. `hermes update`를 실행합니다.
7. 생성된 stash object SHA를 기준으로 overlay를 다시 적용하고 stash entry를 제거합니다.
8. overlay의 `tests`를 실행합니다.
9. 성공/실패를 Discord에 보고합니다.

주의사항:

- 이 기능은 로컬 커스텀 패치를 보존하기 위한 것이며, 비밀값이나 개인 설정 파일을 공개 repo에 넣기 위한 기능이 아닙니다.
- `paths`에는 repo 상대 경로만 넣으세요.
- `tests`에는 공개적으로 공유해도 되는 일반 명령만 넣으세요.
- gateway 재시작은 이 updater가 직접 수행하지 않습니다. 업데이트 성공 후 실제 서비스에 반영하려면 별도 운영 절차로 재시작하세요.

## 자동 발견 규칙

수동 설치 스킬을 무조건 자동 등록하지는 않습니다. 오탐을 줄이기 위해 아래 조건을 모두 만족할 때만 등록합니다.

- 현재 `hub-installed` 스킬이 아닐 것
- bundled skill이 아닐 것
- 이미 `tracked-public-skills.json`에 등록된 스킬이 아닐 것
- upstream 검색 결과에서 exact-name 일치 후보가 정확히 1개일 것

이 조건을 만족하지 못하면 자동 등록하지 않습니다. 이런 경우에는 manifest에 직접 추가해야 합니다.

## 1회 실행

macOS / Linux:

```bash
python3 hermes_update_auto.py --config config.json
```

Windows:

```powershell
py -3 .\hermes_update_auto.py --config .\config.json
```

## 릴리즈

- 현재 공개 태그: `v0.1.0`

## macOS launchd

매일 `09:00`에 실행되도록 설치:

```bash
zsh install_macos_launchd.sh 9 0
```

다음 파일이 생성됩니다.

- `~/Library/LaunchAgents/ai.hermes.daily-repo-update.plist`
- `daily-update.stdout.log`
- `daily-update.stderr.log`

## Windows Task Scheduler

매일 `09:00`에 실행되도록 설치:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows_task.ps1 -Time 09:00
```

`HermesDailyRepoUpdate`라는 예약 작업이 생성됩니다.

## Discord 메시지 형식

업데이터는 아래 상태값을 사용합니다.

- `상태: 업데이트 성공`
- `상태: 업데이트 실패`
- `상태: 업데이트 확인 실패`
- `상태: 업데이트 보류`

`notify_on_no_update`가 `true`면 아래 상태도 전송합니다.

- `상태: 업데이트 없음`

성공 메시지에는 다음 정보가 함께 들어갑니다.

- 반영된 repo 커밋 수
- 반영된 공개 스킬 수

여기서 공개 스킬 수에는 다음이 포함됩니다.

- `hermes skills update`로 갱신된 hub-installed 공개 스킬
- `tracked-public-skills.json`에 자동 발견되었거나 수동 등록된 공개 스킬
