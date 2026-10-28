<#
.SYNOPSIS
    Cross-platform sync and cleanup utility between local development workspaces and Google Drive via rclone.
.DESCRIPTION
    Reads target pairs from sync-targets.conf and executes pull, push, check, or cleanup diagnostics.
.EXAMPLE
    .\sync-projects.ps1 -Action diagnose -Target projects
    .\sync-projects.ps1 -Action cleanup -Target phone_projects -DryRun
    .\sync-projects.ps1 -Action pull -Target all
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet("pull", "push", "check", "diagnose", "cleanup")]
    [string]$Action,

    [Parameter(Position=1)]
    [string]$Target = "all",

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Determine base paths portably
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LocalBase = Split-Path -Parent $ScriptDir
$ConfigFile = Join-Path $ScriptDir "sync-targets.conf"
$ConfigExample = Join-Path $ScriptDir "sync-targets.conf.example"
$FilterFile = Join-Path $ScriptDir "rclone-ignore.txt"

if (-not (Test-Path $ConfigFile)) {
    if (Test-Path $ConfigExample) {
        Write-Warning "sync-targets.conf not found. Creating from sync-targets.conf.example..."
        Copy-Item $ConfigExample $ConfigFile
    } else {
        throw "Configuration file not found: $ConfigFile"
    }
}

if (-not (Test-Path $FilterFile)) {
    throw "Filter rules file not found: $FilterFile"
}

# Parse targets configuration
$Targets = @{}
Get-Content $ConfigFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            $name = $parts[0].Trim()
            $mapping = $parts[1].Trim() -split '\|', 2
            if ($mapping.Count -eq 2) {
                $rawLocal = $mapping[0].Trim().Replace('$LOCAL_BASE', $LocalBase)
                $rawRemote = $mapping[1].Trim()
                $Targets[$name] = @{
                    Local  = [System.IO.Path]::GetFullPath($rawLocal)
                    Remote = $rawRemote
                }
            }
        }
    }
}

if ($Targets.Count -eq 0) {
    throw "No valid targets found in $ConfigFile"
}

function Execute-TargetAction ($targetName, $cfg) {
    $localPath = $cfg.Local
    $remotePath = $cfg.Remote

    Write-Host "`n========================================================" -ForegroundColor Cyan
    Write-Host "Target: $targetName" -ForegroundColor Cyan
    Write-Host "Local : $localPath"
    Write-Host "Remote: $remotePath"
    Write-Host "Action: $Action $(if ($DryRun) { '[DRY-RUN]' } else { '' })"
    Write-Host "========================================================" -ForegroundColor Cyan

    $baseFlags = @("-P")
    if ($DryRun) { $baseFlags += "--dry-run" }

    switch ($Action) {
        "diagnose" {
            Write-Host "`n[1/2] Checking remote connectivity & sample structure..." -ForegroundColor Yellow
            & rclone lsd $remotePath --max-depth 2

            Write-Host "`n[2/2] Scanning remote for ignored build artifacts / bloat..." -ForegroundColor Yellow
            $patterns = @("**/node_modules/", "**/build/", "**/target/", "**/.gradle/", "**/.dart_tool/", "**/Pods/", "**/.venv/")
            foreach ($p in $patterns) {
                $dirs = & rclone lsf $remotePath --dirs-only --recursive --include $p
                if ($dirs) {
                    Write-Host "  Found remote artifact: $p" -ForegroundColor Magenta
                    $dirs | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkGray }
                }
            }
        }

        "cleanup" {
            Write-Host "Cleaning up ignored transient build folders on remote..." -ForegroundColor Yellow
            $cleanupRules = @(
                "--include", "**/node_modules/**",
                "--include", "**/target/**",
                "--include", "**/build/**",
                "--include", "**/.gradle/**",
                "--include", "**/.dart_tool/**",
                "--include", "**/Pods/**",
                "--include", "**/DerivedData/**",
                "--include", "**/.venv/**",
                "--include", "**/__pycache__/**",
                "--include", "**/*.apk",
                "--include", "**/*.aab",
                "--include", "**/.DS_Store",
                "--include", "**/Thumbs.db",
                "--rmdirs"
            )
            $cmdArgs = @("delete", $remotePath) + $cleanupRules + $baseFlags
            & rclone @cmdArgs
        }

        "check" {
            if (-not (Test-Path $localPath)) {
                Write-Warning "Local directory does not exist yet: $localPath"
                return
            }
            & rclone check $localPath $remotePath --filter-from $FilterFile
        }

        "pull" {
            if (-not (Test-Path $localPath)) {
                New-Item -ItemType Directory -Path $localPath -Force | Out-Null
            }
            $cmdArgs = @("copy", $remotePath, $localPath, "--filter-from", $FilterFile) + $baseFlags
            & rclone @cmdArgs
        }

        "push" {
            if (-not (Test-Path $localPath)) {
                throw "Cannot push: local directory does not exist at $localPath"
            }
            $cmdArgs = @("copy", $localPath, $remotePath, "--filter-from", $FilterFile) + $baseFlags
            & rclone @cmdArgs
        }
    }
}

if ($Target -eq "all") {
    foreach ($k in $Targets.Keys) {
        Execute-TargetAction $k $Targets[$k]
    }
} else {
    if (-not $Targets.ContainsKey($Target)) {
        throw "Unknown target '$Target'. Available targets: $($Targets.Keys -join ', ')"
    }
    Execute-TargetAction $Target $Targets[$Target]
}

Write-Host "`nCompleted action '$Action' successfully." -ForegroundColor Green
