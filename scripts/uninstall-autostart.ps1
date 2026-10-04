# Stop starting the C64 Ultimate AI Console at sign-in, and stop the running copy.
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1

Stop-ScheduledTask -TaskName 'C64 AI Console' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'C64 AI Console' -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' -or $_.CommandLine -like '*run-console.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output 'Auto-start removed and the console stopped.'
