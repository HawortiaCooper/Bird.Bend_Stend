@echo off
rem Build the Bird Bend Stand Windows distribution (03_SW\dist\BirdBendStand-<ver>\ + zip). Owner: Implementer B.
rem Wrapper around build_dist.ps1 (bypasses the default PowerShell execution policy); arguments are passed on,
rem e.g.  build_dist.bat -InstallDeps -Installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_dist.ps1" %*
exit /b %ERRORLEVEL%
