# deslab: stop only the Archicad started by ac_start.ps1 (PID from file). No save.
param([string]$PidFile = "$env:TEMP\deslab-ac.pid", [int]$Timeout = 40, [int]$Port = 19723)
if (-not (Test-Path $PidFile)) { "no pid file, nothing to stop"; return }
$id = [int](Get-Content $PidFile)
$p = Get-Process -Id $id -ErrorAction SilentlyContinue
if (-not $p -or $p.ProcessName -notlike "archicad*") { "PID $id not running"; Remove-Item $PidFile; return }
"1" | Set-Content "$env:TEMP\deslab-ac-stop.flag"
$body = @{ command = "API.ExecuteAddOnCommand"; parameters = @{ addOnCommandId = @{ commandNamespace = "TapirCommand"; commandName = "QuitArchicad" }; addOnCommandParameters = @{} } } | ConvertTo-Json -Depth 10 -Compress
try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" -TimeoutSec 10 | Out-Null } catch { "Tapir quit failed: $_" }
$sw = [Diagnostics.Stopwatch]::StartNew()
while (-not $p.HasExited -and $sw.Elapsed.TotalSeconds -lt $Timeout) { Start-Sleep -Milliseconds 500 }
if (-not $p.HasExited) { "graceful quit timed out, killing PID $id"; Stop-Process -Id $id -Force }
Start-Sleep -Seconds 1
Get-Process GSReport -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item $PidFile -ErrorAction SilentlyContinue
Remove-Item "$env:TEMP\deslab-ac-stop.flag" -ErrorAction SilentlyContinue
"stopped in {0:N1}s" -f $sw.Elapsed.TotalSeconds
