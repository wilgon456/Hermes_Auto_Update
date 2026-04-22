param(
    [string]$Time = "09:00"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ScriptDir "config.json"

if (-not (Test-Path $ConfigPath)) {
    Write-Error "config.json not found at $ConfigPath. Copy config.example.json to config.json first."
    exit 1
}

$PythonCmd = (Get-Command py -ErrorAction SilentlyContinue)
if ($PythonCmd) {
    $PythonExe = $PythonCmd.Source
    $Arguments = "-3 `"$ScriptDir\hermes_update_auto.py`" --config `"$ConfigPath`""
} else {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
    $Arguments = "`"$ScriptDir\hermes_update_auto.py`" --config `"$ConfigPath`""
}

$TaskName = "HermesDailyRepoUpdate"

schtasks /Delete /TN $TaskName /F *> $null
schtasks /Create `
    /SC DAILY `
    /TN $TaskName `
    /TR "`"$PythonExe`" $Arguments" `
    /ST $Time `
    /F | Out-Null

Write-Host "Installed Windows Scheduled Task: $TaskName"
Write-Host "Schedule: daily at $Time"
