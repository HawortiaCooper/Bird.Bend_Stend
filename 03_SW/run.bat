@echo off
rem Bird Bend Stand GUI, starts disconnected (D-06: no COM port opened unless selected). Owner: Implementer B.
cd /d "%~dp0"
set PYTHONPATH=%~dp0src;%PYTHONPATH%
"%~dp0..\.venv\Scripts\python.exe" -m bend_stand %*
