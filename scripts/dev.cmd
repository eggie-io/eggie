@echo off
rem Runs dev-windows.ps1 past the execution policy, which blocks .ps1 files by default.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev-windows.ps1" %*
