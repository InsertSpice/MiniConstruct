[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Prepare','Publish')][string]$Operation,
    [string]$Version,
    [string]$Source = 'HEAD',
    [string]$DevRepo = $PSScriptRoot,
    [string]$PublicRepo,
    [string]$GitHubRepo,
    [string]$Output,
    [string]$Manifest,
    [string]$PythonExe,
    [switch]$DryRun,
    [switch]$ReviewWorkingTree
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if (-not $PythonExe) { $PythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
    if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) { throw 'Python is unavailable. Supply -PythonExe with Python 3.12 or newer.' }
    $arguments = @((Join-Path $PSScriptRoot 'tools/release.py'), $Operation.ToLowerInvariant())
    if ($Operation -eq 'Publish') {
        if (-not $Manifest) { throw 'Publish requires -Manifest from a verified Prepare.' }
        if ($DryRun -or $ReviewWorkingTree -or $Version -or $Output) { throw 'Preparation options cannot be used with Publish.' }
        $arguments += @('--manifest', $Manifest)
    } else {
        if ($Manifest) { throw '-Manifest is only valid for Publish.' }
        $arguments += @('--dev', $DevRepo, '--source', $Source)
        if ($Version) { $arguments += @('--version', $Version) }
        if ($PublicRepo) { $arguments += @('--staging', $PublicRepo) }
        if ($GitHubRepo) { $arguments += @('--github', $GitHubRepo) }
        if ($Output) { $arguments += @('--output', $Output) }
        if ($DryRun) { $arguments += '--dry-run' }
        if ($ReviewWorkingTree) { $arguments += '--review-working-tree' }
    }
    & $PythonExe @arguments
    exit $LASTEXITCODE
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
