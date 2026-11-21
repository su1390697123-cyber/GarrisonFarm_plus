@echo off
chcp 65001 >nul
cd /d "%~dp0"
title GarrisonFarm - 个人优化版v1.2
set "GF_LAUNCH_MODE=check"
powershell.exe -NoLogo -NoProfile -Command "$s=[IO.File]::ReadAllText('launcher.ps1',[Text.Encoding]::UTF8); & ([scriptblock]::Create($s))"
pause
