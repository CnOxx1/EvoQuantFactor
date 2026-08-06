#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "[factor-agent] bootstrap from $Root"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 Docker，请先安装 Docker Desktop / Docker Engine。"
}

docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "需要 Docker Compose v2（docker compose）。"
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[factor-agent] 已生成 .env，请编辑并填写 API_TOKEN / LLM_API_KEY 后重新执行本脚本。"
    Write-Host "  编辑文件: $Root\.env"
    exit 2
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    $pair = $_.Split("=", 2)
    if ($pair.Length -eq 2) {
        [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), "Process")
    }
}

if (-not $env:LLM_API_KEY) {
    Write-Warning "LLM_API_KEY 为空，服务可启动但无法真实调用模型。"
}

New-Item -ItemType Directory -Force -Path "data\saved", "data\runs" | Out-Null

$profileArgs = @()
if ($env:BOOTSTRAP_PROFILE -eq "split") {
    $profileArgs = @("--profile", "split")
    $env:WORKER_ENABLED = "false"
    Write-Host "[factor-agent] 使用 split profile：API + 独立 worker/collector"
}

Write-Host "[factor-agent] building & starting..."
docker compose @profileArgs up -d --build

$port = if ($env:APP_PORT) { $env:APP_PORT } else { "8080" }
Write-Host "[factor-agent] waiting health..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            Write-Host "[factor-agent] OK  http://127.0.0.1:$port/health"
            Write-Host "[factor-agent] Docs http://127.0.0.1:$port/docs"
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

Write-Warning "健康检查超时，请执行: docker compose logs -f factor-api"
exit 1
