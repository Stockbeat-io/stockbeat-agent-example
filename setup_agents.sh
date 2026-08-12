#!/usr/bin/env bash
set -euo pipefail

AGENTS_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILES_DIR="$AGENTS_DIR/profiles"
VENV_PYTHON="$AGENTS_DIR/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    exit 1
fi

for profile in "$PROFILES_DIR"/*.json; do
    NAME=$(python3 -c "import json; print(json.load(open('$profile'))['name'])")
    HOUR=$(python3 -c "import json; print(json.load(open('$profile'))['schedule_hour'])")
    MINUTE=$(python3 -c "import json; print(json.load(open('$profile'))['schedule_minute'])")

    LABEL="com.stockbeat.agent.${NAME}"
    PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

    cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "$AGENTS_DIR" &amp;&amp; "$VENV_PYTHON" main.py --agent ${NAME}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>${HOUR}</integer>
    <key>Minute</key><integer>${MINUTE}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/tmp/stockbeat-${NAME}.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/stockbeat-${NAME}.err.log</string>
</dict>
</plist>
PLIST_EOF

    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "✓ Loaded ${LABEL} — runs daily at ${HOUR}:$(printf '%02d' ${MINUTE}) Israel time"
done

echo ""
echo "All agents scheduled. Use 'launchctl list | grep stockbeat' to verify."
