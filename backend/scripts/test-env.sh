#!/usr/bin/env bash
# Standalone SuperBrain test environment (separate from ~/.superbrain-server).
#
# Does NOT touch the live LaunchAgent, live DB, or port 5000.
# Full AI dry-run/recategorize of the live corpus is intentionally NOT run here
# (that spends model tokens). Use validate + unit/smoke tests instead.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_DIR=${SUPERBRAIN_TEST_DIR:-"$HOME/.superbrain-server-test"}
TEST_PORT=${SUPERBRAIN_TEST_PORT:-5055}
SOURCE_PY=${SUPERBRAIN_TEST_PYTHON:-"$HOME/.superbrain-server/.venv/bin/python"}
LIVE_DIR=${SUPERBRAIN_RUNTIME_DIR:-"$HOME/.superbrain-server"}
PID_FILE="$TEST_DIR/test-api.pid"
LOG_FILE="$TEST_DIR/test-api.log"

usage() {
  cat <<EOF
Usage: $0 <command>

Commands:
  setup      Create isolated test dir, install example taxonomy, seed DB
  validate   Validate taxonomy config (no AI tokens)
  test       Run unit tests against taxonomy/classifier
  start      Start API on TEST_PORT ($TEST_PORT) using test DB
  stop       Stop the test API
  smoke      Hit /health, /taxonomy, /categories on the test API
  status     Show test env paths and process state
  promote-plan
             Print the later live cutover steps (does not execute)
  teardown   Stop test API and remove TEST_DIR (asks for confirmation)

Env overrides:
  SUPERBRAIN_TEST_DIR   default: ~/.superbrain-server-test
  SUPERBRAIN_TEST_PORT  default: 5055
  SUPERBRAIN_TEST_PYTHON
EOF
}

need_python() {
  if [[ ! -x "$SOURCE_PY" ]]; then
    echo "Python not found at $SOURCE_PY" >&2
    echo "Set SUPERBRAIN_TEST_PYTHON to a venv that has backend deps." >&2
    exit 1
  fi
}

cmd_setup() {
  need_python
  mkdir -p "$TEST_DIR/config" "$TEST_DIR/static/uploads" "$TEST_DIR/reports"
  cp "$ROOT/config/categories.toml.example" "$TEST_DIR/config/categories.toml"
  if [[ ! -f "$TEST_DIR/token.txt" ]]; then
    # Distinct from live token; fine for local smoke tests.
    printf 'test-taxonomy-token\n' > "$TEST_DIR/token.txt"
    chmod 600 "$TEST_DIR/token.txt"
  fi
  # Empty isolated DB — never copy live DB while another agent may be using it.
  rm -f "$TEST_DIR/superbrain.db" "$TEST_DIR/superbrain.db-wal" "$TEST_DIR/superbrain.db-shm"
  (
    cd "$ROOT"
    DATABASE_PATH="$TEST_DIR/superbrain.db" \
    SUPERBRAIN_CATEGORIES_CONFIG="$TEST_DIR/config/categories.toml" \
    "$SOURCE_PY" - <<'PY'
from core.database import Database
from core.taxonomy import get_taxonomy, clear_taxonomy_cache

clear_taxonomy_cache()
tax = get_taxonomy()
db = Database()
samples = [
    ("YT_test_sysadmin", "Sysadmin", "WireGuard on Debian", "Install and enable WireGuard VPN"),
    ("YT_test_science", "Science", "CRISPR overview", "Gene editing research summary"),
    ("YT_test_tech", "Technology", "Gadget rumors", "Interesting but non-actionable tech news"),
    ("YT_test_history", "History", "Roman roads", "How Roman roads were built"),
    ("YT_test_humanities", "Humanities", "Stoicism primer", "Philosophy and culture"),
    ("YT_test_politics", "Politics", "Election briefing", "Policy and civic affairs"),
    ("YT_test_other", "Other", "Random clip", "Does not fit elsewhere"),
]
for shortcode, category, title, summary in samples:
    db.save_analysis(
        shortcode=shortcode,
        url=f"https://www.youtube.com/watch?v={shortcode[-11:]}",
        username="test",
        title=title,
        summary=summary,
        tags=["#test"],
        music="",
        category=category,
        content_type="youtube",
        category_source="manual",
        category_confidence=1.0,
        category_rationale="seed fixture",
        category_suggestions_json=[],
        category_taxonomy_version=tax.version,
    )
print(f"Seeded {len(samples)} fixtures into test DB")
print("Taxonomy:", ", ".join(tax.names))
PY
  )
  cat > "$TEST_DIR/env.sh" <<EOF
# Source this before talking to the test API.
export SUPERBRAIN_TEST_DIR="$TEST_DIR"
export SUPERBRAIN_TEST_PORT="$TEST_PORT"
export DATABASE_PATH="$TEST_DIR/superbrain.db"
export TOKEN_FILE="$TEST_DIR/token.txt"
export SUPERBRAIN_CATEGORIES_CONFIG="$TEST_DIR/config/categories.toml"
export PORT="$TEST_PORT"
export HOST=127.0.0.1
export ENVIRONMENT=development
export DISABLE_API_DOCS=0
EOF
  # Wire production as read-only reference for transcripts/metadata reuse.
  LIVE_DB="${LIVE_DIR}/superbrain.db"
  if [[ -f "$LIVE_DB" ]]; then
    echo "export SUPERBRAIN_REFERENCE_DATABASE_PATH=\"$LIVE_DB\"" >> "$TEST_DIR/env.sh"
  fi
  # Prefer local omlx on :8000 (matches production). Do not copy secret files into git.
  echo 'export OMLX_HOST="${OMLX_HOST:-http://localhost:8000}"' >> "$TEST_DIR/env.sh"
  if [[ -f "$LIVE_DIR/config/.api_keys" ]]; then
    # Import key material into the test process environment via a root-owned copy
    # outside the git checkout. Values are never printed.
    cp "$LIVE_DIR/config/.api_keys" "$TEST_DIR/config/.api_keys"
    chmod 600 "$TEST_DIR/config/.api_keys"
    if ! grep -q '^OMLX_HOST=' "$TEST_DIR/config/.api_keys"; then
      printf '\nOMLX_HOST=http://localhost:8000\n' >> "$TEST_DIR/config/.api_keys"
    fi
    # Point model_router at the test config dir by symlinking into a sidecar
    # path used only when SUPERBRAIN_API_KEYS_FILE is set (see model_router).
    echo "export SUPERBRAIN_API_KEYS_FILE=\"$TEST_DIR/config/.api_keys\"" >> "$TEST_DIR/env.sh"
  fi
  echo "Test environment ready at $TEST_DIR (port $TEST_PORT)"
  echo "Live runtime left untouched: $LIVE_DIR"
}

cmd_validate() {
  need_python
  if [[ ! -f "$TEST_DIR/config/categories.toml" ]]; then
    echo "Run: $0 setup" >&2
    exit 1
  fi
  (
    cd "$ROOT"
    SUPERBRAIN_CATEGORIES_CONFIG="$TEST_DIR/config/categories.toml" \
    DATABASE_PATH="$TEST_DIR/superbrain.db" \
    "$SOURCE_PY" scripts/recategorize.py \
      --config "$TEST_DIR/config/categories.toml" \
      --database "$TEST_DIR/superbrain.db" \
      validate
  )
}

cmd_test() {
  need_python
  (
    cd "$ROOT"
    SUPERBRAIN_CATEGORIES_CONFIG="$TEST_DIR/config/categories.toml" \
    DATABASE_PATH="$TEST_DIR/superbrain.db" \
    "$SOURCE_PY" -m unittest tests.test_taxonomy -v
  )
}

cmd_start() {
  need_python
  if [[ ! -f "$TEST_DIR/env.sh" ]]; then
    echo "Run: $0 setup first" >&2
    exit 1
  fi
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Test API already running (pid $(cat "$PID_FILE"))"
    exit 0
  fi
  # Refuse to bind the live port.
  if [[ "$TEST_PORT" == "5000" ]]; then
    echo "Refusing to start test API on live port 5000" >&2
    exit 2
  fi
  # shellcheck disable=SC1090
  source "$TEST_DIR/env.sh"
  (
    cd "$ROOT"
    nohup "$SOURCE_PY" -m uvicorn api:app \
      --host 127.0.0.1 --port "$TEST_PORT" \
      >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
  )
  sleep 1
  if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Test API started pid=$(cat "$PID_FILE") on http://127.0.0.1:$TEST_PORT"
    echo "log: $LOG_FILE"
  else
    echo "Failed to start test API; see $LOG_FILE" >&2
    exit 1
  fi
}

cmd_stop() {
  if [[ -f "$PID_FILE" ]]; then
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
      echo "Stopped test API pid=$pid"
    fi
    rm -f "$PID_FILE"
  else
    echo "No pid file; nothing to stop"
  fi
}

cmd_smoke() {
  token=$(tr -d '[:space:]' <"$TEST_DIR/token.txt")
  base="http://127.0.0.1:$TEST_PORT"
  echo "GET $base/health"
  curl -fsS "$base/health" || curl -fsS "$base/" || true
  echo
  echo "GET $base/taxonomy"
  curl -fsS -H "X-API-Key: $token" "$base/taxonomy" | "$SOURCE_PY" -m json.tool
  echo
  echo "GET $base/categories"
  curl -fsS -H "X-API-Key: $token" "$base/categories" | "$SOURCE_PY" -m json.tool
}

cmd_status() {
  echo "TEST_DIR=$TEST_DIR"
  echo "TEST_PORT=$TEST_PORT"
  echo "LIVE_DIR=$LIVE_DIR (do not touch)"
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "test API: running pid=$(cat "$PID_FILE")"
  else
    echo "test API: stopped"
  fi
  lsof -iTCP:"$TEST_PORT" -sTCP:LISTEN 2>/dev/null || true
  lsof -iTCP:5000 -sTCP:LISTEN 2>/dev/null | head -3 || true
}

cmd_promote_plan() {
  cat <<EOF
Later cutover (ONLY after operator approval; live playlist/agent must be idle):

1. Stop live LaunchAgent: launchctl bootout gui/\$(id -u)/com.djbclark.superbrain
   (or the brew/launchctl stop variant you normally use)
2. Backup live DB:
   python $ROOT/scripts/recategorize.py --database $LIVE_DIR/superbrain.db backup
3. Deploy reviewed code to live runtime:
   $ROOT/scripts/deploy-local.sh
4. Install taxonomy config (do not overwrite blindly if customized):
   cp $TEST_DIR/config/categories.toml $LIVE_DIR/config/categories.toml
5. Recategorize live metadata (THIS spends AI tokens):
   python $ROOT/scripts/recategorize.py --database $LIVE_DIR/superbrain.db \\
     --config $LIVE_DIR/config/categories.toml dry-run --out /tmp/sb-recat.jsonl --progress
   # review, then:
   python $ROOT/scripts/recategorize.py --database $LIVE_DIR/superbrain.db \\
     --config $LIVE_DIR/config/categories.toml apply --from-report /tmp/sb-recat.jsonl \\
     --i-understand-this-writes-categories
6. Restart live LaunchAgent.
7. Point Android app at live server; confirm /taxonomy pills.
8. Teardown test env: $0 teardown

Do NOT copy the tiny seeded test DB over the live DB.
EOF
}

cmd_teardown() {
  cmd_stop || true
  if [[ ! -d "$TEST_DIR" ]]; then
    echo "No test dir at $TEST_DIR"
    exit 0
  fi
  read -r -p "Delete $TEST_DIR ? type yes: " ans
  if [[ "$ans" == "yes" ]]; then
    rm -rf "$TEST_DIR"
    echo "Removed $TEST_DIR"
  else
    echo "Aborted"
    exit 1
  fi
}

cmd=${1:-}
case "$cmd" in
  setup) cmd_setup ;;
  validate) cmd_validate ;;
  test) cmd_test ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  smoke) cmd_smoke ;;
  status) cmd_status ;;
  promote-plan) cmd_promote_plan ;;
  teardown) cmd_teardown ;;
  *) usage; exit 2 ;;
esac
