# Put a host gcc on PATH for `pio test -e native` (FW_design §2.2, §9.4): CLion-bundled MinGW GCC 13.1.
# Usage (PowerShell, repo root):  . 02_FW\tools\host_env.ps1 ; .venv\Scripts\pio test -d 02_FW -e native
# Origin: Thrust_Stand_HAW/02_FW/tools/host_env.ps1 @37c8747 (copied unchanged).
$clion = Get-ChildItem "C:\Program Files\JetBrains" -Directory -Filter "CLion*" -ErrorAction SilentlyContinue |
         Sort-Object Name -Descending | Select-Object -First 1
if ($null -eq $clion) { Write-Error "CLion MinGW not found; install a host gcc and put it on PATH"; return }
$mingw = Join-Path $clion.FullName "bin\mingw\bin"
if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $mingw })) { $env:PATH = "$mingw;$env:PATH" }
Write-Host "host gcc: $mingw"
