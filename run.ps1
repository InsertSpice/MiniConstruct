param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8743,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    $PythonCommand = $VenvPython
} else {
    $PythonCommand = "py"
}

$Arguments = @()
if ($PythonCommand -eq "py") { $Arguments += "-3.12" }
$Arguments += @("-m", "miniconstruct", "--host", $BindHost, "--port", $Port)
if ($Reload) { $Arguments += "--reload" }

Set-Location -LiteralPath $ProjectRoot
& $PythonCommand @Arguments

