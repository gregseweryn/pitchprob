@echo off
REM Double-click entry point for the pitchprob dashboard (Windows -> WSL).
REM
REM Copy this file to the Desktop, or right-click -> "Pin to taskbar".
REM It opens a console window with the freshness gate and the server logs;
REM closing that window stops the dashboard.
title pitchprob
wsl.exe -d Ubuntu -- bash /home/greg/projects/trading/scripts/start.sh
REM Keep the window open if something failed, so the error stays readable
REM instead of vanishing with the console.
if errorlevel 1 pause
