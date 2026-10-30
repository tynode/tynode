$desk = "C:\AI\tynode\apps\desktop"; $port = 8787
$pm = Select-String -Path "$desk\main.js" -Pattern 'const PORT = (\d+)' | Select-Object -First 1
if ($pm) { $port = [int]$pm.Matches[0].Groups[1].Value }
$u = "http://127.0.0.1:$port"
try { $cfg = Invoke-RestMethod "$u/v1/auth/config" } catch { Write-Host "Motor calismiyor ($u). Once Tynode uygulamasini ac."; exit }
Write-Host ("config: " + ($cfg | ConvertTo-Json -Compress))
if (-not $cfg.otp) { Write-Host "OTP ayarli degil (.env eksik?)."; exit }
$email = (Read-Host "Gmail adresin").Trim()
try {
  Invoke-RestMethod "$u/v1/auth/otp/send" -Method Post -ContentType "application/json" -Body (@{email=$email} | ConvertTo-Json) | Out-Host
} catch { Write-Host ("Gonderilemedi: " + $_.ErrorDetails.Message); exit }
$code = (Read-Host "Mailden gelen kod").Trim()
try {
  Invoke-RestMethod "$u/v1/auth/otp/verify" -Method Post -ContentType "application/json" -Body (@{email=$email; token=$code} | ConvertTo-Json) | Out-Host
} catch { Write-Host ("Dogrulanamadi: " + $_.ErrorDetails.Message) }