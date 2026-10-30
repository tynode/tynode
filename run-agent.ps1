$base = "C:\AI\tynode"; $con = "$base\apps\console"; $desk = "$base\apps\desktop"
$port = 8787
$pm = Select-String -Path "$desk\main.js" -Pattern 'const PORT = (\d+)' | Select-Object -First 1
if ($pm) { $port = [int]$pm.Matches[0].Groups[1].Value }
$env:TYNODE_URL = "http://127.0.0.1:$port"
try { Invoke-WebRequest "$env:TYNODE_URL/v1/auth/me" -UseBasicParsing -TimeoutSec 3 | Out-Null } catch { if (-not $_.Exception.Response) { Write-Host "Tynode motoru calismiyor. Once uygulamayi ac."; exit } }
$sec = Read-Host "LLM API key (xai-... / gsk_... / sk-...)" -AsSecureString
$key = [System.Net.NetworkCredential]::new('', $sec).Password
$env:LLM_API_KEY = $key
$m = Read-Host "Model (bos birak = otomatik sec)"
if (-not $m) {
  $baseUrl = if ($key -like 'xai-*') { 'https://api.x.ai/v1' } elseif ($key -like 'gsk_*') { 'https://api.groq.com/openai/v1' } else { 'https://api.openai.com/v1' }
  try {
    $ids = (Invoke-RestMethod "$baseUrl/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 15).data.id
    $bad = 'whisper|orpheus|guard|safeguard|embed|image|imagine|tts|audio|moderation|dall|realtime|transcribe|allam|vision'
    $chat = @($ids | Where-Object { $_ -notmatch $bad })
    $prefer = 'gpt-oss-120b','grok-4','grok-3','llama-3.3','gpt-4o','gpt-oss-20b','qwen'
    foreach ($p in $prefer) { $hit = $chat | Where-Object { $_ -like "*$p*" } | Select-Object -First 1; if ($hit) { $m = $hit; break } }
    if (-not $m -and $chat.Count -gt 0) { $m = $chat[0] }
  } catch { Write-Host "Model listesi alinamadi, scriptin kendi secimine birakiliyor." }
}
if ($m) { $env:LLM_MODEL = $m; Write-Host "Secilen model: $m" } else { Remove-Item Env:LLM_MODEL -ErrorAction SilentlyContinue }
$venvPy = "$base\.venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "python" }
Push-Location $con
& $py agent_demo.py
Pop-Location
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:LLM_MODEL -ErrorAction SilentlyContinue
