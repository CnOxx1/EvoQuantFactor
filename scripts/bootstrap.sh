#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[factor-agent] bootstrap from $ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: 未检测到 Docker，请先安装 Docker 后再运行。"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: 需要 Docker Compose v2（docker compose）。"
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[factor-agent] 已生成 .env，请编辑并填写 LLM_API_KEY 后重新执行本脚本。"
  echo "  编辑文件: $ROOT/.env"
  exit 2
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "WARNING: LLM_API_KEY 为空，服务可启动但无法真实调用模型。"
fi

mkdir -p data/saved data/runs

PROFILE_ARGS=()
if [[ "${BOOTSTRAP_PROFILE:-}" == "full" ]]; then
  PROFILE_ARGS=(--profile full)
  echo "[factor-agent] 使用 full profile（mcp + redis）"
fi

echo "[factor-agent] building & starting..."
docker compose "${PROFILE_ARGS[@]}" up -d --build

echo "[factor-agent] waiting health..."
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:${APP_PORT:-8080}/health" >/dev/null 2>&1; then
    echo "[factor-agent] OK  http://127.0.0.1:${APP_PORT:-8080}/health"
    echo "[factor-agent] Docs http://127.0.0.1:${APP_PORT:-8080}/docs"
    exit 0
  fi
  sleep 2
done

echo "WARNING: 健康检查超时，请执行: docker compose logs -f factor-api"
exit 1
