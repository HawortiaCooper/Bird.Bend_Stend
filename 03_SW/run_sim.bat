@echo off
rem Bird Bend Stand GUI connected to the in-process FW simulator (SYS-008). Owner: Implementer B.
cd /d "%~dp0"
set PYTHONPATH=%~dp0src;%PYTHONPATH%
"%~dp0..\.venv\Scripts\python.exe" -m bend_stand --sim %*
