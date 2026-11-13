# deslab: fast and reliable Archicad 27 start for self-check runs.
# Usage: powershell -File ac_start.ps1 [-Template path] [-Timeout 180] [-NoLibrary]
param(
  [string]$Template = "C:\Program Files\GRAPHISOFT\Archicad 27\Значения по умолчанию\Archicad\Общий шаблон Archicad 27.tpl",
  [string]$Exe = "C:\Program Files\GRAPHISOFT\Archicad 27\Archicad.exe",
  [int]$Timeout = 180,
  [switch]$NoLibrary,
  [string]$Library = "C:\Program Files\GRAPHISOFT\Archicad 27\Библиотека Archicad 27",
  [string]$PidFile = "$env:TEMP\deslab-ac.pid"
)
$ErrorActionPreference = "Stop"
function Tapir($cmd, $params = @{}, $port = 19723) {
  $body = @{ command = "API.ExecuteAddOnCommand"; parameters = @{ addOnCommandId = @{ commandNamespace = "TapirCommand"; commandName = $cmd }; addOnCommandParameters = $params } } | ConvertTo-Json -Depth 10 -Compress
  Invoke-RestMethod -Uri "http://127.0.0.1:$port" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" -TimeoutSec 10
}
$sw = [Diagnostics.Stopwatch]::StartNew()
function Lap($m) { "{0,6:N1}s  {1}" -f $sw.Elapsed.TotalSeconds, $m }
function Fail($m) { Lap "ERROR: $m"; throw $m }

if (Get-Process archicad -ErrorAction SilentlyContinue) { Fail "Archicad.exe already running (not started by this script). Do not touch, report PID." }
Get-Process GSReport -ErrorAction SilentlyContinue | Stop-Process -Force
# leftovers of crashed runs make Archicad ask «Восстановление Проекта», and "Продолжить" there crashed it (ACUIData.dll):
# the autosave folders are moved aside (not deleted) while no Archicad runs
$quarantine = Join-Path $env:USERPROFILE ("GRAPHISOFT\autosave-quarantine-" + (Get-Date -Format yyyyMMdd-HHmmss))
Get-ChildItem (Join-Path $env:USERPROFILE "GRAPHISOFT") -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "АвтоСохранение-27*" } | ForEach-Object {
  New-Item -ItemType Directory $quarantine -Force | Out-Null; Move-Item $_.FullName (Join-Path $quarantine $_.Name) -Force; Lap "autosave folder $($_.Name) moved to $quarantine" }
$wdPid = if (Test-Path "$env:TEMP\deslab-ac-watchdog.pid") { [int](Get-Content "$env:TEMP\deslab-ac-watchdog.pid") } else { 0 }
if (-not (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
  # separate process, keeps running after this script ends (WMI Create did not start it: pwsh is a Store app)
  Start-Process (Get-Command pwsh).Source -ArgumentList '-NoProfile','-File',(Join-Path $PSScriptRoot 'ac_watchdog.ps1') -WindowStyle Hidden
  Lap "watchdog started"; Start-Sleep -Seconds 3 }
if (Test-Path "$env:TEMP\deslab-ac-watchdog.log") { $wdSeenInit = @(Get-Content "$env:TEMP\deslab-ac-watchdog.log").Count } else { $wdSeenInit = 0 }
Remove-Item "$env:TEMP\deslab-ac-stop.flag" -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $Exe -ArgumentList "`"$Template`"" -PassThru
$p.Id | Set-Content $PidFile
Lap "started PID $($p.Id)"
$closed = @(); $wdLog = "$env:TEMP\deslab-ac-watchdog.log"; $wdSeen = $wdSeenInit; $alive = $false; $ready = $false; $tapirPort = 0
while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
  Start-Sleep -Milliseconds 500
  if ($p.HasExited) { Fail "Archicad exited with code $($p.ExitCode)" }
  if (Test-Path $wdLog) { $L = @(Get-Content $wdLog -Encoding UTF8); for ($k = $wdSeen; $k -lt $L.Count; $k++) { Lap "watchdog: $($L[$k])"; if ($L[$k] -match "ACTION") { $closed += $L[$k] } ; if ($L[$k] -match "UNKNOWN") { Fail "unknown modal window: $($L[$k])" } }; $wdSeen = $L.Count }
  if (-not $alive) { foreach ($port in 19723..19730) { try { $null = Tapir "GetProjectInfo" @{} $port; $alive = $true; $tapirPort = $port; Lap "Tapir answers on $port"; break } catch {} } }
  if ($alive) { $ready = $true; break }
}
if (-not $ready) { Fail "Tapir did not answer within $Timeout s (closed dialogs: $($closed -join ', '))" }
# let startup dialogs finish, close them again
Start-Sleep -Seconds 2
if (-not $NoLibrary) {
  $libs = (Tapir "GetLibraries" @{} $tapirPort).result.addOnCommandResponse.libraries
  $have = $libs | Where-Object { $_.path -like "*Библиотека Archicad 27*" }
  if (-not $have) {
    Lap "main library missing, adding"
    $null = Tapir "AddLibraries" @{ libraries = @(@{ path = $Library }) } $tapirPort
    $null = Tapir "ReloadLibraries" @{} $tapirPort
    Lap "library added and reloaded"
  } else { Lap "main library present" }
}
Lap ("READY port=$tapirPort pid=$($p.Id) closedDialogs=[" + ($closed -join '; ') + "]")
