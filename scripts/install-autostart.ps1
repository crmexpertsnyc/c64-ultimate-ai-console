# Start the C64 Ultimate AI Console automatically when you sign in to Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
# Remove again with scripts\uninstall-autostart.ps1.

$runner = Join-Path $PSScriptRoot 'run-console.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'C64 AI Console' -Description 'C64 Ultimate AI Console web app (port 8064)' `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "Installed: the console starts at sign-in. Start it now with: Start-ScheduledTask -TaskName 'C64 AI Console'"
