#!/usr/bin/env bash
# Supervises qfactor.agent.supervisor on a persistent Linux host.
# Usage: scripts/factor_factory_monitor.sh {start|stop|status|supervise}
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INTERVAL_SECONDS="${FACTOR_INTERVAL_SECONDS:-300}"
DISCOVERY_EVERY="${FACTOR_DISCOVERY_EVERY:-12}"
SCREENED_EVERY="${FACTOR_SCREENED_EVERY:-72}"
LLM_RATIO="${FACTOR_LLM_RATIO:-0.25}"
# A candidate evaluation may legitimately take a few minutes. Treat no heartbeat
# beyond two cycles plus a five-minute allowance as stalled rather than looping.
MAX_STALE_SECONDS="${FACTOR_MAX_STALE_SECONDS:-900}"
RUNTIME="$ROOT/runs/factory_monitor"
PID_FILE="$RUNTIME/monitor.pid"
WORKER_PID_FILE="$RUNTIME/worker.pid"
STATUS_FILE="$RUNTIME/status.json"
STOP_FILE="$RUNTIME/STOP"
MONITOR_LOG="$RUNTIME/monitor.log"
RESTART_LOG="$RUNTIME/restarts.jsonl"
mkdir -p "$RUNTIME"

now_epoch() { date +%s; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$MONITOR_LOG"; }
is_pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
read_pid() { [ -f "$1" ] && tr -dc '0-9' < "$1" || true; }

health_json() {
  local mpid wpid status_age state
  mpid="$(read_pid "$PID_FILE")"
  wpid="$(read_pid "$WORKER_PID_FILE")"
  status_age=-1
  state="missing"
  if [ -f "$STATUS_FILE" ]; then
    status_age=$(( $(now_epoch) - $(stat -c %Y "$STATUS_FILE") ))
    state="$(grep -o '"state"[[:space:]]*:[[:space:]]*"[^"]*"' "$STATUS_FILE" | head -n1 | cut -d'"' -f4 || true)"
  fi
  printf '{"monitor_pid":%s,"monitor_alive":%s,"worker_pid":%s,"worker_alive":%s,"status_age_seconds":%s,"state":"%s","stop_requested":%s}\n' \
    "${mpid:-null}" "$(is_pid_alive "$mpid" && echo true || echo false)" \
    "${wpid:-null}" "$(is_pid_alive "$wpid" && echo true || echo false)" \
    "$status_age" "$state" "$( [ -f "$STOP_FILE" ] && echo true || echo false )"
}

run_worker() {
  rm -f "$STOP_FILE"
  cd "$ROOT"
  "$PYTHON_BIN" -m qfactor.agent.supervisor run-forever \
    --interval-seconds "$INTERVAL_SECONDS" \
    --discovery-every "$DISCOVERY_EVERY" \
    --screened-every "$SCREENED_EVERY" \
    --llm-ratio "$LLM_RATIO" >> "$MONITOR_LOG" 2>&1 &
  local worker=$!
  printf '%s\n' "$worker" > "$WORKER_PID_FILE"
  log "worker_started pid=$worker interval=$INTERVAL_SECONDS discovery_every=$DISCOVERY_EVERY screened_every=$SCREENED_EVERY llm_ratio=$LLM_RATIO" >&2
  echo "$worker"
}

supervise() {
  printf '%s\n' "$$" > "$PID_FILE"
  trap 'touch "$STOP_FILE"; [ -f "$WORKER_PID_FILE" ] && kill "$(read_pid "$WORKER_PID_FILE")" 2>/dev/null || true; exit 0' INT TERM
  local restarts=0 backoff=10
  log "monitor_started pid=$$"
  while [ ! -f "$STOP_FILE" ]; do
    local worker start_ts stale=0
    worker="$(run_worker)"
    start_ts="$(now_epoch)"
    while is_pid_alive "$worker" && [ ! -f "$STOP_FILE" ]; do
      sleep 30
      if [ -f "$STATUS_FILE" ]; then
        local age
        age=$(( $(now_epoch) - $(stat -c %Y "$STATUS_FILE") ))
        if [ "$age" -gt "$MAX_STALE_SECONDS" ]; then
          stale=1
          log "worker_stalled pid=$worker status_age=$age max_stale=$MAX_STALE_SECONDS"
          kill -TERM "$worker" 2>/dev/null || true
          sleep 10
          is_pid_alive "$worker" && kill -KILL "$worker" 2>/dev/null || true
          break
        fi
      fi
    done
    wait "$worker" 2>/dev/null || true
    rm -f "$WORKER_PID_FILE"
    [ -f "$STOP_FILE" ] && break
    restarts=$((restarts + 1))
    printf '{"at":"%s","restart":%d,"worker_pid":%s,"stalled":%s,"uptime_seconds":%d,"backoff_seconds":%d}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$restarts" "$worker" \
      "$( [ "$stale" -eq 1 ] && echo true || echo false )" "$(( $(now_epoch) - start_ts ))" "$backoff" >> "$RESTART_LOG"
    log "worker_exit_restart restart=$restarts stalled=$stale backoff=$backoff"
    sleep "$backoff"
    backoff=$((backoff * 2))
    [ "$backoff" -gt 300 ] && backoff=300
  done
  rm -f "$WORKER_PID_FILE" "$PID_FILE"
  log "monitor_stopped"
}

start() {
  local existing
  existing="$(read_pid "$PID_FILE")"
  if is_pid_alive "$existing"; then
    echo "already_running $(health_json)"
    return 0
  fi
  rm -f "$STOP_FILE" "$PID_FILE" "$WORKER_PID_FILE"
  nohup "$0" supervise >/dev/null 2>&1 &
  local monitor=$!
  sleep 1
  echo "started monitor_pid=$monitor $(health_json)"
}

stop() {
  touch "$STOP_FILE"
  local monitor
  monitor="$(read_pid "$PID_FILE")"
  if is_pid_alive "$monitor"; then
    kill -TERM "$monitor" 2>/dev/null || true
  fi
  echo "stop_requested $(health_json)"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) health_json ;;
  supervise) supervise ;;
  *) echo "Usage: $0 {start|stop|status|supervise}" >&2; exit 2 ;;
esac
