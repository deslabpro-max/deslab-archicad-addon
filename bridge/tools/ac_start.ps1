# deslab: fast and reliable Archicad start for self-check runs (27 by default, -Version 29 for Archicad 29).
# Usage: powershell -File ac_start.ps1 [-Version 29] [-Template path] [-Timeout 180] [-NoLibrary]
param(
  [int]$Version = 27,
  [string]$Template = "",
  [string]$Exe = "",
  [int]$Timeout = 0,
  [switch]$NoLibrary,
  [string]$Library = "",
  [string]$PidFile = "$env:TEMP\deslab-ac.pid"
)
$root = "C:\Program Files\GRAPHISOFT\Archicad $Version"
if (-not $Exe) { $Exe = "$root\Archicad.exe" }
if (-not $Template) { $Template = if ($Version -ge 29) { "$root\Значения по умолчанию\Archicad\Шаблон Archicad $Version.tpl" } else { "$root\Значения по умолчанию\Archicad\Общий шаблон Archicad $Version.tpl" } }
if (-not $Library) { $Library = "$root\Библиотека Archicad $Version" }
# Archicad 29 opens its JSON port minutes after the start (13.11.2026: 240 s were not enough) and ships its library as packages
if (-not $Timeout) { $Timeout = if ($Version -ge 29) { 900 } else { 180 } }
if ($Version -ge 29) { $NoLibrary = $true }
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
Get-ChildItem (Join-Path $env:USERPROFILE "GRAPHISOFT") -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "АвтоСохранение-$Version*" } | ForEach-Object {
  New-Item -ItemType Directory $quarantine -Force | Out-Null; Move-Item $_.FullName (Join-Path $quarantine $_.Name) -Force; Lap "autosave folder $($_.Name) moved to $quarantine" }
$wdPid = if (Test-Path "$env:TEMP\deslab-ac-watchdog.pid") { [int](Get-Content "$env:TEMP\deslab-ac-watchdog.pid") } else { 0 }
if (-not (Get-Process -Id $wdPid -ErrorAction SilentlyContinue)) {
  # separate process, keeps running after this script ends (WMI Create did not start it: pwsh is a Store app)
  Start-Process (Get-Command pwsh).Source -ArgumentList '-NoProfile','-File',(Join-Path $PSScriptRoot 'ac_watchdog.ps1') -WindowStyle Hidden
  Lap "watchdog started"; Start-Sleep -Seconds 3 }
if (Test-Path "$env:TEMP\deslab-ac-watchdog.log") { $wdSeenInit = @(Get-Content "$env:TEMP\deslab-ac-watchdog.log").Count } else { $wdSeenInit = 0 }
Remove-Item "$env:TEMP\deslab-ac-stop.flag", "$env:TEMP\deslab-ac-ready.flag" -ErrorAction SilentlyContinue
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
  $have = $libs | Where-Object { $_.path -like "*Библиотека Archicad $Version*" }
  if (-not $have) {
    Lap "main library missing, adding"
    $null = Tapir "AddLibraries" @{ libraries = @(@{ path = $Library }) } $tapirPort
    $null = Tapir "ReloadLibraries" @{} $tapirPort
    Lap "library added and reloaded"
  } else { Lap "main library present" }
}
"1" | Set-Content "$env:TEMP\deslab-ac-ready.flag"   # from now on the watchdog leaves save dialogs to the user
Lap ("READY port=$tapirPort pid=$($p.Id) closedDialogs=[" + ($closed -join '; ') + "]")
