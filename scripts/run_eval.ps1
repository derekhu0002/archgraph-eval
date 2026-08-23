<#
.SYNOPSIS
    Run Terminal-Bench evaluations with the ARCHGRAPH harness.

.DESCRIPTION
    Wraps `harbor run` with the archgraph agent adapter.

    Smoke (one easy task, one trial):
        .\scripts\run_eval.ps1 -Task openssl-selfsigned-cert -Trials 1

    Full 89-task run:
        .\scripts\run_eval.ps1 -Full

.PARAMETER Dataset
    Harbor dataset id. Default: terminal-bench/terminal-bench-2-1

.PARAMETER Task
    Single task id (e.g. openssl-selfsigned-cert). When omitted, runs all tasks.

.PARAMETER Trials
    Number of trials per task (default 1).

.PARAMETER Model
    Underlying model id (default dashscope/qwen3-32b).

.PARAMETER Full
    Switch: run the whole dataset (ignores -Task).
#>
param(
    [string]$Dataset = "terminal-bench/terminal-bench-2-1",
    [string]$Task = "",
    [int]$Trials = 1,
    [string]$Model = "dashscope/qwen3-32b",
    [switch]$Full,
    [switch]$Seed,
    [string]$JobsDir = "jobs"
)

# Round-1/2 seed set (one task per family + an easy git task).
$SeedTasks = @(
    "git-leak-recovery",
    "crack-7z-hash",
    "count-dataset-tokens",
    "sqlite-db-truncate",
    "fix-git"
)

$ErrorActionPreference = "Stop"

# Load QWEN_KEY from the global argo .env (never echo the value).
$envFile = "$env:USERPROFILE\.argo\.env"
if (Test-Path $envFile -and -not $env:QWEN_KEY) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"'), "Process")
        }
    }
}

$agent = "harbor_agents.archgraph_agent:ArchGraphAgent"

if ($Full) {
    Write-Host "Running full dataset: $Dataset  (agent=$agent, model=$Model, trials=$Trials)"
    harbor run -d $Dataset --agent $agent -m $Model -n $Trials
    exit $LASTEXITCODE
}

if (-not $Task) {
    Write-Host "No -Task given. Running smoke task: openssl-selfsigned-cert"
    $Task = "openssl-selfsigned-cert"
}

Write-Host "Running task: $Task  (agent=$agent, model=$Model, trials=$Trials)"
harbor run -d $Dataset `
    --task $Task `
    --agent $agent `
    -m $Model `
    -k $Trials

exit $LASTEXITCODE
