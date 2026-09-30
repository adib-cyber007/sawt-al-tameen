# Register a current-user task to restore the hosted backend and ngrok after sign-in.
# Run once from the repository root: powershell -File scripts/install_hosted_autostart.ps1
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$startScript = Join-Path $PSScriptRoot 'start_hosted.ps1'
$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = 'SawtAlTameenHosted'

$action = New-ScheduledTaskAction -Execute $powerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$recoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
    -RepetitionInterval (New-TimeSpan -Minutes 3)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigger, $recoveryTrigger) `
    -Principal $principal -Settings $settings -Description 'Restore Sawt al-Tameen at sign-in and check every three minutes' `
    -Force | Out-Null
Write-Output "Registered $taskName for $user sign-in and recovery every three minutes."
Write-Output 'The task starts the local backend and ngrok only while this computer is signed in and online.'
