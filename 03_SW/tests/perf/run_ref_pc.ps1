# Reference-PC performance runs PR-1...PR-5 (SW_test_plan section 6.1; SRS NFR-001...004, SAF-SW-001).
# Run from the repository root in an interactive desktop session (real display, keyboard; not over RDP):
#   powershell -ExecutionPolicy Bypass -File 03_SW\tests\perf\run_ref_pc.ps1 [-Out D:\perf_ref] [-SoakS 3600] [-Quick]
# The PC must otherwise be idle: close other applications, no builds / test suites running.
# Nothing here opens a COM port (D-06): the app connects to the out-of-process simulator through a loopback sniffer.
# Every child process is stopped by its own handle (PID logged in <run>\processes.log).
# Verifies: NFR-001, NFR-002, NFR-003, NFR-004, SAF-SW-001 (procedure)
param(
    [string]$Out = "$env:TEMP\bbs_perf_ref_$(Get-Date -Format yyyyMMdd_HHmm)",
    [int]$SoakS = 3600,
    [switch]$Quick           # 60 s / 20 presses / 20 trips / 300 s soak: a dry run of the procedure only
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
$G = Join-Path $PSScriptRoot "perf_gui.py"
New-Item -ItemType Directory -Force $Out | Out-Null
$pr1 = 600; $n = 100; $soak = $SoakS; $ref = 15
if ($Quick) { $pr1 = 60; $n = 20; $soak = 300; $ref = 2 }

# PC description for the report
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$gpu = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join "; "
$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$os = "{0}.{1} {2}" -f (Get-ItemProperty $k).CurrentBuild, (Get-ItemProperty $k).UBR, (Get-ItemProperty $k).DisplayVersion
$pyv = & $Py -c "import sys,PySide6,pyqtgraph,numpy;print(sys.version.split()[0],'PySide6',PySide6.__version__,'pyqtgraph',pyqtgraph.__version__,'numpy',numpy.__version__)"
"CPU: $cpu`nRAM: $ram GB`nGPU: $gpu`nWindows: $os`nPython: $pyv" | Out-File -Encoding utf8 (Join-Path $Out "pc_spec.txt")

function Run([string]$name, [string[]]$a) {
    Write-Host "== $name ($(Get-Date -Format T))"
    & $Py $G --out (Join-Path $Out $name) @a
    if ($LASTEXITCODE -ne 0) { Write-Warning "$name exited with $LASTEXITCODE (see $name\results.json)" }
}
Run "A_all"    @("--plots", "all",    "--pr1-s", "$pr1", "--pr2", "$n", "--pr3", "$n")   # PR-1 (TC-NFR-001-01) + PR-2 + PR-3
Run "B_all600" @("--plots", "all600", "--pr1-s", "$pr1")                                 # PR-1 (TC-NFR-001-03)
Run "C_four"   @("--plots", "four",   "--pr1-s", "$pr1")                                 # PR-1 (TC-NFR-001-04)
Run "D_pr5"    @("--plots", "all",    "--pr5", "$n")                                     # PR-5 (TC-SAF-SW-001-01 rt)
Run "E_soak"   @("--plots", "all",    "--soak-s", "$soak", "--soak-ref-min", "$ref")     # PR-4 (TC-NFR-004-01)
& $Py (Join-Path $PSScriptRoot "perf_summary.py") (Join-Path $Out "A_all") (Join-Path $Out "B_all600") `
    (Join-Path $Out "C_four") (Join-Path $Out "D_pr5") (Join-Path $Out "E_soak") | Tee-Object (Join-Path $Out "summary.md")
Write-Host "Results: $Out  (send the whole folder to Validator F)"
