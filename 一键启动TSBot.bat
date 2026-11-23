@echo off
chcp 65001 >nul
title TS6 Music Bot Launcher (Normal / Hachimi Brain)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-tsbot.ps1" %*
if errorlevel 1 (
    echo.
    pause
)
