@echo off
REM Fallback launcher — only needed when the canonical Nexus.exe is blocked
REM by SmartScreen / corporate policy on first run. Functionally identical
REM to double-clicking Nexus.exe: spawns the tray launcher detached with
REM no console window.

setlocal
set HERE=%~dp0
start "" "%HERE%python\pythonw.exe" "%HERE%tray.pyw"
endlocal
