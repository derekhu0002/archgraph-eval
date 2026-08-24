# Pull the 8 Round-3c task images with retry/backoff.
$imgs = @(
    'alexgshaw/git-multibranch:20251031',
    'alexgshaw/nginx-request-logging:20251031',
    'alexgshaw/pypi-server:20251031',
    'alexgshaw/build-cython-ext:20251031',
    'alexgshaw/dna-insert:20251031',
    'alexgshaw/chess-best-move:20251031',
    'alexgshaw/vulnerable-secret:20251031',
    'alexgshaw/large-scale-text-editing:20251031'
)

foreach ($i in $imgs) {
    $short = ($i -split ':')[0] -split '/' | Select-Object -Last 1
    if (docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -Quiet $i) {
        Write-Output "$short ALREADY_PRESENT"
        continue
    }
    $ok = $false
    foreach ($try in 1..3) {
        Write-Output "$short TRY$try"
        $job = Start-Job -ScriptBlock { param($img) docker pull $img 2>&1 | Out-String } -ArgumentList $i
        if (Wait-Job $job -Timeout 240) {
            Receive-Job $job | Select-Object -Last 1
            Remove-Job $job -Force
            if (docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -Quiet $i) {
                $ok = $true
                Write-Output "$short OK"
                break
            }
        } else {
            Stop-Job $job
            Remove-Job $job -Force
            Write-Output "$short TRY${try}_TIMEOUT"
        }
        Start-Sleep -Seconds 10
    }
    if (-not $ok) { Write-Output "$short FAILED_AFTER_3" }
}
Write-Output "PULL_DONE"
