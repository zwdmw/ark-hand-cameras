@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Test-Cameras.ps1" -OpenBrowser
if errorlevel 1 pause
