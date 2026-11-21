@echo off
chcp 65001 >nul
cd /d "%~dp0"
title GarrisonFarm - 重新配置连接
set "GF_LAUNCH_MODE=configure"
powershell.exe -NoLogo -NoProfile -Command "$s=[IO.File]::ReadAllText('launcher.ps1',[Text.Encoding]::UTF8); & ([scriptblock]::Create($s))"
pause
