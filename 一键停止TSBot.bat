@echo off
chcp 65001 >nul
title Stop TS6 Music Bot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-tsbot.ps1" %*
timeout /t 3 >nul
