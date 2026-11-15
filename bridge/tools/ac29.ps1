# deslab: terminal-only AC29 test sessions. No mouse, watchdog, autosave moves or global process kills.
# pwsh -File bridge/tools/ac29.ps1 start|status|run|stop [-InputFile scan.e57] [-Discard]
param(
  [ValidateSet('start', 'status', 'run', 'stop')][string]$Action = 'status',
  [string]$InputFile = '',
  [int]$Timeout = 180,
  [switch]$Discard
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ac29_dialogs.ps1')
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
$bridge = Split-Path $PSScriptRoot
$stateFile = Join-Path $workspace 'captures/ac29-session.json'
$exe = 'C:\Program Files\GRAPHISOFT\Archicad 29\Archicad.exe'
$template = 'C:\Program Files\GRAPHISOFT\Archicad 29\Значения по умолчанию\Archicad\Шаблон Archicad 29.tpl'
$timer = [Diagnostics.Stopwatch]::StartNew()
function Log($message) { Write-Host ('{0:N1}s {1}' -f $timer.Elapsed.TotalSeconds, $message) }
function Save-State($session) { $session | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8 }
function Owned-Process($session) {
  if (-not $session) { return $null }
  $proc = Get-Process -Id $session.pid -ErrorAction SilentlyContinue
  if ($proc -and $proc.Path -eq $exe -and $proc.StartTime.ToUniversalTime().Ticks.ToString() -eq $session.startTicks) { return $proc }
  return $null
}
function Owned-Ports($proc) {
  @(Get-NetTCPConnection -State Listen -OwningProcess $proc.Id -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 19723 -and $_.LocalPort -le 19743 } |
    Select-Object -ExpandProperty LocalPort -Unique)
}
function Request($port, $command, $parameters = @{}, $requestTimeout = 3) {
  $body = @{command=$command; parameters=$parameters} | ConvertTo-Json -Depth 15 -Compress
  $reply = Invoke-RestMethod "http://127.0.0.1:$port" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json' -TimeoutSec $requestTimeout
  if (-not $reply.succeeded) { throw ($reply.error | ConvertTo-Json -Compress) }
  $reply.result
}
function Tapir($port, $command, $parameters = @{}, $requestTimeout = 3) {
  $result = Request $port 'API.ExecuteAddOnCommand' @{addOnCommandId=@{commandNamespace='TapirCommand'; commandName=$command}; addOnCommandParameters=$parameters} $requestTimeout
  $answer = $result.addOnCommandResponse
  if ($answer.error -or $answer.success -eq $false) { throw ($answer | ConvertTo-Json -Compress) }
  $answer
}
$session = if (Test-Path -LiteralPath $stateFile) { Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } else { $null }
$proc = Owned-Process $session
if ($Action -eq 'start') {
  if (-not $proc) {
    if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $template)) { throw 'Archicad 29 or its template is missing.' }
    New-Item -ItemType Directory -Path (Split-Path $stateFile) -Force | Out-Null
    $previousAutoClose = $env:TAPIR_DIALOG_AUTOCLOSE
    try {
      $env:TAPIR_DIALOG_AUTOCLOSE = 'true'
      $proc = Start-Process -FilePath $exe -ArgumentList "`"$template`"" -WindowStyle Hidden -PassThru
    } finally { $env:TAPIR_DIALOG_AUTOCLOSE = $previousAutoClose }
    $session = [pscustomobject]@{pid=$proc.Id; startTicks=$proc.StartTime.ToUniversalTime().Ticks.ToString(); port=0; template=$template; ready=$false}
    Save-State $session
    Log "Started own test instance PID=$($proc.Id)"
  }
  $nextLog = $timer.Elapsed.TotalSeconds + 20
  while ($timer.Elapsed.TotalSeconds -lt $Timeout) {
    $proc.Refresh()
    if ($proc.HasExited) { throw "Archicad exited with code $($proc.ExitCode); session recorded in $stateFile" }
    foreach ($line in [DeslabStartupDialogs]::Inspect($proc.Id, $true)) { Log $line }
    foreach ($port in (Owned-Ports $proc)) {
      try {
        $product = Request $port 'API.GetProductInfo'
        if ($product.version -ne 29) { throw "Unexpected version: $($product.version)" }
        $null = Tapir $port 'GetProjectInfo'
        $null = Tapir $port 'SetDialogAutoClose' @{enabled=$true}
        $session.port = $port
        $session.ready = $true
        Save-State $session
        Log "READY version=29 pid=$($proc.Id) port=$port"
        return
      } catch { $lastError = $_.Exception.Message }
    }
    if ($timer.Elapsed.TotalSeconds -ge $nextLog) { Log "Waiting for API, PID=$($proc.Id)"; $nextLog += 20 }
    Start-Sleep -Milliseconds 750
  }
  throw "API not ready after $Timeout seconds. PID=$($proc.Id). Last error: $lastError. Use stop -Discard to discard this test session."
}
if (-not $proc) {
  if ($Action -eq 'run') { throw 'No owned AC29 session. Run start first.' }
  if ($Action -eq 'stop' -and (Test-Path -LiteralPath $stateFile)) { Remove-Item -LiteralPath $stateFile }
  Log 'No owned AC29 session is running.'
  return
}
$ports = Owned-Ports $proc
if ($Action -eq 'status') {
  $session | ConvertTo-Json -Compress
  [DeslabStartupDialogs]::Inspect($proc.Id, $false)
  if ($session.port -in $ports) { Request $session.port 'API.GetProductInfo' | ConvertTo-Json -Compress }
  return
}
if ($Action -eq 'stop') {
  if ($session.port -in $ports) {
    try { $null = Tapir $session.port 'QuitArchicad' } catch { Log 'Quit request ended; checking process.' }
  }
  if (-not $proc.WaitForExit(10000)) {
    if (-not $Discard) { throw 'Archicad is still running. To discard this owned test project, run stop -Discard.' }
    # Recheck identity immediately before terminating; never kill another PID or a reused PID.
    $proc = Owned-Process $session
    if ($proc) { Stop-Process -InputObject $proc -Force; $proc.WaitForExit(); Log 'Discarded owned test session.' }
  }
  Remove-Item -LiteralPath $stateFile
  Log 'STOPPED'
  return
}
if (-not $session.ready -or $session.port -notin $ports) { throw 'Owned session API is not ready. Run start again.' }
if (-not $InputFile) { throw 'run requires -InputFile (E57 path or rebuild preset).' }
$python = Join-Path $bridge '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Bridge environment not found: $python" }
$previousPort = $env:DESLAB_ARCHICAD_PORT
try {
  $env:DESLAB_ARCHICAD_PORT = [string]$session.port
  Log 'Applying office standard to the owned test project.'
  $standard = Tapir $session.port 'ApplyOfficeStandard' @{
    sourceProjectPath=(Join-Path $workspace 'Антон Крючков.pln')
    packagePath=(Join-Path $workspace 'deslab-standard/standard.json')
    updateExisting=$true
  } 180
  $standard | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath (Join-Path $workspace 'captures/ac29-standard.json') -Encoding utf8
  foreach ($line in $standard.report) { Write-Host $line }
  Push-Location $bridge
  try {
    & $python -m deslab_bridge.rebuild $InputFile --root $workspace --model codex:gpt-6-astra
    if ($LASTEXITCODE -ne 0) { throw "Bridge failed with exit code $LASTEXITCODE" }
  } finally { Pop-Location }
} finally { $env:DESLAB_ARCHICAD_PORT = $previousPort }
