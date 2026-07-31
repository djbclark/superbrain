#!/usr/bin/env bash
# Capture compliance screenshots with headless Chrome (macOS / Linux).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT="$ROOT/docs/compliance-screenshots"
mkdir -p "$OUT"

CHROME=""
for c in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v google-chrome || true)" \
  "$(command -v chromium || true)" \
  "$(command -v chromium-browser || true)"
do
  if [[ -n "$c" && -x "$c" ]]; then CHROME="$c"; break; fi
done
if [[ -z "$CHROME" ]]; then
  echo "Chrome/Chromium not found" >&2
  exit 1
fi

shot() {
  local name="$1" url="$2"
  local tmp="$OUT/.tmp-$name"
  mkdir -p "$tmp"
  "$CHROME" --headless=new --disable-gpu --window-size=1280,1600 \
    --screenshot="$OUT/$name" --virtual-time-budget=8000 "$url" >/dev/null 2>&1 || true
  rm -rf "$tmp"
  if [[ -f "$OUT/$name" ]]; then
    echo "wrote $OUT/$name"
  else
    echo "FAILED $name ($url)" >&2
  fi
}

# Prefer local HTML for reliable capture; GitHub blob pages also work when online.
COMPLIANCE="file://$ROOT/docs/youtube-api-compliance.html"
shot "01-homepage-compliance.png" "$COMPLIANCE"
shot "02-privacy-policy.png" "https://github.com/djbclark/superbrain/blob/main/docs/PRIVACY.md"
shot "03-terms-of-service.png" "https://github.com/djbclark/superbrain/blob/main/docs/TERMS.md"
shot "05-oauth-revoke-hint.png" "https://myaccount.google.com/permissions"

echo "Manual captures still needed:"
echo "  04-oauth-consent.png  -> run: superbrain --youtube-connect  (photograph Google consent + scopes)"
echo "  06-video-thumbnail-ui.png -> Android/app UI showing a YouTube analysis with thumbnail"
