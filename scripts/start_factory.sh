#!/usr/bin/env bash
# Boot the CSI100 factor factory in the documented order:
#   pull main → load .env → prepare-data → start supervisor
# prepare-data must pass (mining_allowed) before any LLM work starts.
#
# Usage:
#   scripts/start_factory.sh                 # same as factory
#   scripts/start_factory.sh factory         # daemon via factor_factory_monitor.sh
#   scripts/start_factory.sh factory-fg      # foreground supervisor
#   scripts/start_factory.sh produce         # one clean produce run
#   scripts/start_factory.sh prepare         # pull + prepare-data only
#   scripts/start_factory.sh status|stop
#   scripts/start_factory.sh help
#
# Env:
#   SKIP_PULL=1          do not git fetch/checkout/pull
#   SKIP_PREPARE=1       skip the data gate (not recommended)
#   DRY_RUN=1            print the plan and exit
#   QFACTOR_BIN          override CLI (tests / custom venv)
#   PYTHON_BIN           override interpreter
#   FACTOR_START_CYCLE   supervisor first cycle (default 12)
#   FACTOR_ROUNDS        produce rounds (default 5)
#   FACTOR_BATCH_SIZE    produce batch size (default 8)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMMAND="${1:-factory}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_PULL="${SKIP_PULL:-0}"
SKIP_PREPARE="${SKIP_PREPARE:-0}"
START_CYCLE="${FACTOR_START_CYCLE:-12}"
INTERVAL_SECONDS="${FACTOR_INTERVAL_SECONDS:-300}"
DISCOVERY_EVERY="${FACTOR_DISCOVERY_EVERY:-12}"
SCREENED_EVERY="${FACTOR_SCREENED_EVERY:-72}"
LLM_RATIO="${FACTOR_LLM_RATIO:-}"
ROUNDS="${FACTOR_ROUNDS:-5}"
BATCH_SIZE="${FACTOR_BATCH_SIZE:-8}"
MONITOR="$ROOT/scripts/factor_factory_monitor.sh"
BOOT_LOG="$ROOT/runs/factory_monitor/boot.log"

log() {
  mkdir -p "$(dirname "$BOOT_LOG")"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$BOOT_LOG"
}

die() {
  log "error: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/start_factory.sh [factory|factory-fg|produce|prepare|status|stop|help]

factory      pull main, prepare-data, then start the supervised factory
factory-fg   same checks, then run supervisor in the foreground
produce      pull main, prepare-data, then one clean produce run
prepare      pull main and run prepare-data only
status       factory monitor heartbeat
stop         request a clean factory stop

Environment: SKIP_PULL=1 SKIP_PREPARE=1 DRY_RUN=1 QFACTOR_BIN=... PYTHON_BIN=...
EOF
}

resolve_python() {
  if [ -n "${PYTHON_BIN:-}" ]; then
    return 0
  fi
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
}

qfactor() {
  if [ -n "${QFACTOR_BIN:-}" ]; then
    "$QFACTOR_BIN" "$@"
    return
  fi
  if [ -x "$ROOT/.venv/bin/qfactor" ]; then
    "$ROOT/.venv/bin/qfactor" "$@"
    return
  fi
  resolve_python
  "$PYTHON_BIN" -m qfactor.cli "$@"
}

load_env() {
  if [ ! -f "$ROOT/.env" ]; then
    return 0
  fi
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|\#*) continue ;;
      export\ *) line="${line#export }" ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    [ -n "$key" ] || continue
    if [ -z "${!key+x}" ]; then
      export "$key=$val"
    fi
  done < "$ROOT/.env"
}

require_openai_key() {
  load_env
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    die "OPENAI_API_KEY is empty. Put it in .env before factory/produce."
  fi
}

pull_main() {
  if [ "$SKIP_PULL" = "1" ]; then
    log "skip_pull=1"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run git fetch/checkout/pull origin main"
    return 0
  fi
  git fetch origin main
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "main" ]; then
    log "checkout main from $branch"
    git checkout main
  fi
  git pull origin main
  log "on $(git rev-parse --abbrev-ref HEAD) $(git rev-parse --short HEAD)"
}

ensure_db() {
  local db="$ROOT/data/qfactor.sqlite3"
  if [ -f "$db" ]; then
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run db-init"
    return 0
  fi
  log "database missing; running db-init"
  qfactor db-init
}

prepare_data() {
  if [ "$SKIP_PREPARE" = "1" ]; then
    log "skip_prepare=1 (mining gate disabled)"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run prepare-data && data-contract-readiness"
    return 0
  fi
  log "prepare-data: inspect coverage, sync if incomplete, check contracts"
  set +e
  qfactor prepare-data
  local rc=$?
  set -e
  if [ "$rc" -eq 2 ]; then
    die "prepare-data blocked mining (window incomplete or research contract failed). Fix data, then rerun."
  fi
  if [ "$rc" -ne 0 ]; then
    die "prepare-data failed with exit $rc"
  fi
  qfactor data-contract-readiness || true
  log "prepare-data passed; mining_allowed"
}

start_factory_daemon() {
  export PYTHON_BIN
  export FACTOR_START_CYCLE="$START_CYCLE"
  export FACTOR_INTERVAL_SECONDS="$INTERVAL_SECONDS"
  export FACTOR_DISCOVERY_EVERY="$DISCOVERY_EVERY"
  export FACTOR_SCREENED_EVERY="$SCREENED_EVERY"
  if [ -n "$LLM_RATIO" ]; then
    export FACTOR_LLM_RATIO="$LLM_RATIO"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run $MONITOR start start_cycle=$START_CYCLE"
    return 0
  fi
  "$MONITOR" start
}

run_factory_fg() {
  resolve_python
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run $PYTHON_BIN -m qfactor.agent.supervisor run-forever --start-cycle $START_CYCLE"
    return 0
  fi
  exec "$PYTHON_BIN" -m qfactor.agent.supervisor run-forever \
    --interval-seconds "$INTERVAL_SECONDS" \
    --discovery-every "$DISCOVERY_EVERY" \
    --screened-every "$SCREENED_EVERY" \
    --start-cycle "$START_CYCLE" \
    ${LLM_RATIO:+--llm-ratio "$LLM_RATIO"}
}

run_produce() {
  if [ "$DRY_RUN" = "1" ]; then
    log "dry_run produce --rounds $ROUNDS --batch-size $BATCH_SIZE --gate research"
    return 0
  fi
  qfactor produce --rounds "$ROUNDS" --batch-size "$BATCH_SIZE" --gate research
}

case "$COMMAND" in
  help|-h|--help)
    usage
    ;;
  status)
    "$MONITOR" status
    ;;
  stop)
    "$MONITOR" stop
    ;;
  prepare)
    pull_main
    ensure_db
    prepare_data
    ;;
  produce)
    require_openai_key
    pull_main
    ensure_db
    prepare_data
    run_produce
    ;;
  factory|start)
    require_openai_key
    pull_main
    ensure_db
    prepare_data
    resolve_python
    start_factory_daemon
    ;;
  factory-fg)
    require_openai_key
    pull_main
    ensure_db
    prepare_data
    run_factory_fg
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
