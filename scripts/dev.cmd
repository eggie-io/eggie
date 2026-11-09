@echo off
rem Runs dev.ps1 past the execution policy, which blocks .ps1 files by default.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
