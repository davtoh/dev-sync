<#
.SYNOPSIS
    Cross-platform development workspace sync utility via rclone (Windows runner).
.DESCRIPTION
    Forwards execution to the unified Python core engine (dev_sync).
.EXAMPLE
    .\sync-projects.ps1 diagnose projects
    .\sync-projects.ps1 cleanup all -DryRun
    .\sync-projects.ps1 pull all
    .\sync-projects.ps1 push custom_app $HOME/code/my-app gdrive:my-app
#>
[CmdletBinding()]
param (
    [Parameter(Position=0, Mandatory=$false)]
    [string]$Action,

    [Parameter(Position=1, Mandatory=$false)]
    [string]$Target,

    [switch]$DryRun,

    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Discover Python executable
$pythonBin = "python"
if (-not (Get-Command $pythonBin -ErrorAction SilentlyContinue)) {
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $pythonBin = "py"
    } else {
        throw "Python 3 is required to run dev-sync but was not found in PATH."
    }
}

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$ScriptDir;$env:PYTHONPATH" } else { $ScriptDir }

# Build argument list to forward cleanly
$callArgs = @("-m", "dev_sync")
if ($Action) { $callArgs += $Action }
if ($Target) { $callArgs += $Target }
if ($DryRun) { $callArgs += "--dry-run" }
if ($RemainingArgs) { $callArgs += $RemainingArgs }

& $pythonBin @callArgs
exit $LASTEXITCODE
