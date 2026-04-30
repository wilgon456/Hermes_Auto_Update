#!/bin/zsh
set -euo pipefail

SCRIPT_PATH="${(%):-%N}"
SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
CONFIG_PATH="${SCRIPT_DIR}/config.json"
PLIST_PATH="${HOME}/Library/LaunchAgents/ai.hermes.daily-repo-update.plist"
HOUR="${1:-9}"
MINUTE="${2:-0}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "config.json not found at ${CONFIG_PATH}"
  echo "Copy config.example.json to config.json and fill in your values first."
  exit 1
fi

PYTHON_BIN="$(command -v python3)"
mkdir -p "${HOME}/Library/LaunchAgents"

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.hermes.daily-repo-update</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${SCRIPT_DIR}/hermes_update_auto.py</string>
    <string>--config</string>
    <string>${CONFIG_PATH}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${SCRIPT_DIR}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${HOUR}</integer>
    <key>Minute</key>
    <integer>${MINUTE}</integer>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${SCRIPT_DIR}/daily-update.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${SCRIPT_DIR}/daily-update.stderr.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl enable "gui/$(id -u)/ai.hermes.daily-repo-update"
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"

echo "Installed launchd job:"
echo "  ${PLIST_PATH}"
echo "Schedule: daily at $(printf '%02d:%02d' "${HOUR}" "${MINUTE}")"
