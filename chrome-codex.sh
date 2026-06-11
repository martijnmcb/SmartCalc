#!/bin/zsh

set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT="9222"
PROFILE_DIR="$HOME/Library/Application Support/Chrome-Codex-Debug"
VERSION_URL="http://127.0.0.1:${DEBUG_PORT}/json/version"

if [[ ! -x "$CHROME_APP" ]]; then
  echo "Google Chrome executable not found at: $CHROME_APP" >&2
  exit 1
fi

mkdir -p "$PROFILE_DIR"

if curl -fsS "$VERSION_URL" >/dev/null 2>&1; then
  echo "Chrome debug endpoint already available on port $DEBUG_PORT."
  echo "Profile: $PROFILE_DIR"
  exit 0
fi

echo "Starting Chrome debug profile..."
echo "Profile: $PROFILE_DIR"

# Chrome ignores the debugging flag when a regular instance is already active.
pkill -f "Google Chrome" >/dev/null 2>&1 || true
sleep 2

nohup "$CHROME_APP" \
  --remote-debugging-port="$DEBUG_PORT" \
  --user-data-dir="$PROFILE_DIR" \
  >/tmp/chrome-codex.log 2>&1 &

for _ in {1..20}; do
  if curl -fsS "$VERSION_URL" >/dev/null 2>&1; then
    echo "Chrome debug endpoint is ready: $VERSION_URL"
    exit 0
  fi
  sleep 1
done

echo "Chrome started, but the debug endpoint did not come up on $VERSION_URL." >&2
echo "Check /tmp/chrome-codex.log for startup output." >&2
exit 1
