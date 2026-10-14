@echo off
rem Build BirdBendStand-GateRunner.exe into the gate kit (D-57, GR-1). Owner: Implementer B. Arguments are passed on.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_gate_runner.ps1" %*
exit /b %ERRORLEVEL%
