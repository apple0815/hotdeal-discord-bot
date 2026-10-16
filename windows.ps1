param(
    [ValidateSet('Setup', 'Run', 'Preview', 'InstallTask', 'StopTask', 'Status')]
    [string]$Mode = 'Run'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$secretPath = Join-Path $PSScriptRoot 'webhook.secret'
$taskHash = [System.Security.Cryptography.SHA256]::Create()
$folderHash = [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot))).Replace('-', '').Substring(0, 10)
$taskName = 'HotdealDiscord-' + $folderHash

function Initialize-Python {
    if (Test-Path -LiteralPath $venvPython) { return }
    $candidates = @()
    foreach ($name in @('py', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notlike '*WindowsApps*') { $candidates += $command.Source }
    }
    $candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    $runtime = $null
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        & $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $runtime = $candidate; break }
    }
    if (-not $runtime) { throw 'Python 3.10+ is required. Install Python from python.org, then run setup.cmd again.' }
    & $runtime -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}

function Load-Webhook {
    if ($env:DISCORD_WEBHOOK_URL) { return }
    if (-not (Test-Path -LiteralPath $secretPath)) { throw 'Run setup.cmd first.' }
    $secret = Get-Content -LiteralPath $secretPath -Raw | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try { $env:DISCORD_WEBHOOK_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

try {
    switch ($Mode) {
        'Setup' {
            Initialize-Python
            & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
            Write-Host 'Paste the Discord TEXT CHANNEL webhook URL. Input is hidden.'
            Write-Host 'It will be encrypted for this Windows account, and a test message will be sent.'
            $secret = Read-Host 'Webhook URL' -AsSecureString
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            try { $env:DISCORD_WEBHOOK_URL = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim() }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
            & $venvPython (Join-Path $PSScriptRoot 'hotdeal_bot.py') --test-webhook
            if ($LASTEXITCODE -ne 0) { throw 'Webhook validation or test failed. Nothing was saved.' }
            ConvertFrom-SecureString -SecureString $secret | Set-Content -LiteralPath $secretPath -Encoding ASCII
            Write-Host 'Setup complete. Run start.cmd, or install-autostart.cmd for background startup at logon.'
        }
        'Run' {
            Load-Webhook
            if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run setup.cmd first.' }
            & $venvPython -u (Join-Path $PSScriptRoot 'hotdeal_bot.py')
            exit $LASTEXITCODE
        }
        'Preview' {
            if (-not (Test-Path -LiteralPath $venvPython)) {
                Initialize-Python
                & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
                if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
            }
            & $venvPython (Join-Path $PSScriptRoot 'hotdeal_bot.py') --dry-run --once
            exit $LASTEXITCODE
        }
        'Status' {
            if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run setup.cmd first.' }
            & $venvPython (Join-Path $PSScriptRoot 'hotdeal_bot.py') --status
            Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Select-Object TaskName, State
        }
        'InstallTask' {
            if (-not (Test-Path -LiteralPath $secretPath)) { throw 'Run setup.cmd first.' }
            $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $scriptPath = Join-Path $PSScriptRoot 'windows.ps1'
            $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $scriptPath + '" -Mode Run'
            $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $arguments -WorkingDirectory $PSScriptRoot
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
            $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Personal deal notifier. Checks every 10 minutes while Windows is awake and this account is logged in.' -Force | Out-Null
            Start-ScheduledTask -TaskName $taskName
            Write-Host ('Autostart registered and started: ' + $taskName)
            Write-Host 'See data\health.json and data\bot.log. Use stop-autostart.cmd to stop and remove this task.'
        }
        'StopTask' {
            $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            if ($task) {
                Stop-ScheduledTask -TaskName $taskName
                Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            }
            Write-Host 'Autostart task stopped and removed. Foreground start.cmd processes must be stopped with Ctrl+C.'
        }
    }
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    Remove-Item Env:\DISCORD_WEBHOOK_URL -ErrorAction SilentlyContinue
}
