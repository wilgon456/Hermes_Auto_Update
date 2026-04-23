# hermes update auto

로컬 `hermes-agent` 체크아웃을 매일 점검해서 자동 업데이트하는 외부 업데이터입니다.

## 주요 기능

- 매일 `origin/main`을 확인합니다.
- 저장소 업데이트가 있으면 `hermes update`를 실행합니다.
- Hermes 공식 스킬 업데이트와 커스텀 스킬 업데이트를 각각 on/off 할 수 있습니다.
- `~/.hermes/skills` 아래의 수동 설치 스킬 중, 공개 소스에서 정확히 하나로 식별되는 경우 자동으로 추적 목록에 등록합니다.
- 별도 manifest에 등록된 수동 설치 공개 스킬도 upstream 내용을 다시 확인해서 자동 갱신합니다.
- 같은 repo/profile 조합에 대해 중복 실행이 감지되면 새 실행은 바로 종료합니다.
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

## 공개 전 주의사항

- 이 도구는 `hermes update`를 호출하므로, 실제 업데이트 시 Hermes gateway 재시작이나 manual gateway 종료가 일어날 수 있습니다.
- 현재 기본 동작은 최근 gateway 활동이 감지되면 업데이트를 보류하는 것입니다.
- 다만 이 보류 판단은 `gateway_state.json`과 `sessions/sessions.json` 기준이라, 아주 긴 작업을 100% 완벽하게 감지하는 것은 아닙니다.
- 공개 저장소에는 실제 `config.json`, 프로필 `.env`, Discord 토큰 같은 비밀값을 포함하면 안 됩니다.

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
  "defer_if_recent_gateway_activity": true,
  "recent_session_window_seconds": 120,
  "tracked_public_skills_manifest": "/absolute/path/to/tracked-public-skills.json",
  "notify_on_no_update": false
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
  "defer_if_recent_gateway_activity": true,
  "recent_session_window_seconds": 120,
  "tracked_public_skills_manifest": "C:\\Users\\you\\hermes-update-auto\\tracked-public-skills.json",
  "notify_on_no_update": false
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
- `defer_if_recent_gateway_activity`
  기본값은 `true`입니다.
  Hermes gateway 상태 파일과 session index를 보고 최근 활동이 감지되면 이번 실행의 업데이트를 보류합니다.
- `recent_session_window_seconds`
  기본값은 `120`초입니다.
  최근 session activity로 간주할 시간 창입니다.
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
