param([switch]$Cleanup)
$base = "C:\AI\tynode"; $desk = "$base\apps\desktop"
$port = 8787
$pm = Select-String -Path "$desk\main.js" -Pattern 'const PORT = (\d+)' | Select-Object -First 1
if ($pm) { $port = [int]$pm.Matches[0].Groups[1].Value }
$env:TYNODE_URL = "http://127.0.0.1:$port"
$venvPy = "$base\.venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }
$a = @("$base\tools\smoke_test.py"); if ($Cleanup) { $a += "--cleanup" }
& $py @a