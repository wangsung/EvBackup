@echo off
title Evernote BackupManager Server Launcher
chcp 65001 > nul
cls
echo ==========================================================
echo       Evernote BackupManager Web Server Launcher
echo ==========================================================
echo  [*] Starting Flask Web Server on http://127.0.0.1:5001...
echo  [*] Automatically opening dashboard in your browser...
echo ==========================================================
echo.

:: Launch browser in the background after 2 seconds delay to ensure Flask has started
start /b cmd /c "timeout /t 2 > nul && start "" http://127.0.0.1:5001"

:: Start the Flask Web Server
python manager_server.py

pause
