<#
.SYNOPSIS
  Build BirdBendStand-GateRunner.exe (D-57, GR-1): Validator E's hardware-gate runner frozen with PyInstaller
  (console, one-folder) into the gate kit, with the FW twin of the same checkout, then smoke-test it.

.DESCRIPTION
  Owner: Implementer B (packaging; runner code: Validator E, 00_System/tools/hil). Design: GATE_RUNNER.md §1.
  Windows PowerShell 5.1. Steps:
   (1) venv interpreter 3.14 + every installed package = requirements-gaterunner.lock.txt (hash-pinned, SWR-27);
   (2) version = gate_res.RUNNER_VERSION + git hash (dirty if hil / tools / twin / FW sources / params / pictures
       differ from HEAD) -> <BuildDir>\build_info.json;
   (3) FW twin of this checkout: 00_System\tools\fw_twin\build.py --core fw into <BuildDir>\twin (private),
       staged with a normalised PE timestamp;
   (4) PyInstaller (BirdBendStand-GateRunner.spec) -> <Kit>\BirdBendStand-GateRunner\;
   (5) runner exe PE timestamp normalised, runner hash checked against make_approval's computation, BUILD_INFO.txt;
   (6) smoke test (smoke_gate_runner.py: --version, --list, --twin --quick, --verify-zip, refusals, stray-run rule);
   (7) zip next to the folder.
  No COM port is opened (D-06): every smoke run sets BEND_STAND_D06_GUARD and the refusals end before any port.
  Implements: D-57 (frozen gate runner)

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File 03_SW\packaging\gate_runner\build_gate_runner.ps1
  ... -Kit 04_Delivery\GateKit_FW0.8.6 -BuildDir <private dir> [-InstallDeps] [-SkipSmoke] [-FullTwin]
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Kit = "",               # gate kit folder (default: the only 04_Delivery\GateKit_FW* folder)
    [string]$BuildDir = "",          # private work folder (default: 03_SW\build\gate_runner)
    [switch]$InstallDeps,            # pip install --require-hashes -r requirements-gaterunner.lock.txt first
    [switch]$AllowEnvMismatch,
    [switch]$SkipSmoke,
    [switch]$FullTwin,               # smoke G3 with the full twin plan instead of --quick
    [switch]$NoZip
)
$ErrorActionPreference = "Stop"
$t0 = Get-Date
$Here = $PSScriptRoot
$Pack = Split-Path $Here -Parent
$SW = Split-Path $Pack -Parent
$Repo = Split-Path $SW -Parent
if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\python.exe" }
if (-not $BuildDir) { $BuildDir = Join-Path $SW "build\gate_runner" }
$BuildDir = [IO.Path]::GetFullPath($BuildDir)
if (-not $Kit) {
    $kits = @(Get-ChildItem (Join-Path $Repo "04_Delivery") -Directory -Filter "GateKit_FW*")
    if ($kits.Count -ne 1) { throw "pass -Kit: found $($kits.Count) GateKit_FW* folders in 04_Delivery" }
    $Kit = $kits[0].FullName
}
$Kit = [IO.Path]::GetFullPath($Kit)
$InfoJson = Join-Path $BuildDir "build_info.json"
$Stage = Join-Path $BuildDir "stage"
$Dist = Join-Path $Kit "BirdBendStand-GateRunner"
$Lock = Join-Path $SW "requirements-gaterunner.lock.txt"

function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Invoke-Native([string]$what, [scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $cmd } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

Step "1/7 build environment"
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }
$pyver = (& $Python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
Write-Host "python $pyver  ($Python)"
if (-not $pyver.StartsWith("3.14.")) { throw "Python 3.14 required (D-33 i), found $pyver" }
if ($InstallDeps) {
    Invoke-Native "pip install" { & $Python -m pip install --disable-pip-version-check --require-hashes -r $Lock }
}
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $Python (Join-Path $Pack "buildinfo.py") check-env --req $Lock
$envRc = $LASTEXITCODE; $ErrorActionPreference = $prev
if ($envRc -ne 0) {
    if ($AllowEnvMismatch) { Write-Warning "installed versions differ from requirements-gaterunner.lock.txt" }
    else { throw "installed versions differ from requirements-gaterunner.lock.txt; run with -InstallDeps" }
}

Step "2/7 version"
New-Item -ItemType Directory -Force $BuildDir | Out-Null
Invoke-Native "gr_build info" { & $Python (Join-Path $Here "gr_build.py") info --out $InfoJson | Out-Null }
$info = Get-Content $InfoJson -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "runner $($info.version)  ->  $Dist"
if ($info.dirty) { Write-Warning "runner sources differ from git HEAD: the version is marked .dirty (no gate use)" }

Step "3/7 FW twin (same checkout, private build dir)"
Invoke-Native "twin build" {
    & $Python (Join-Path $Here "gr_build.py") stage --info $InfoJson --twin-build-dir (Join-Path $BuildDir "twin") --stage $Stage
}

Step "4/7 PyInstaller"
$env:BBS_GR_INFO = $InfoJson
$env:BBS_GR_STAGE = $Stage
$env:SOURCE_DATE_EPOCH = [string]$info.commit_time
$env:PYTHONHASHSEED = "0"                    # reproducible: fixed member order of base_library.zip
try {
    Invoke-Native "PyInstaller" {
        & $Python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $Kit `
            --workpath (Join-Path $BuildDir "pyinstaller") (Join-Path $Here "BirdBendStand-GateRunner.spec")
    }
} finally {
    Remove-Item Env:\BBS_GR_INFO, Env:\BBS_GR_STAGE, Env:\SOURCE_DATE_EPOCH, Env:\PYTHONHASHSEED -ErrorAction SilentlyContinue
}

Step "5/7 runner hash + BUILD_INFO.txt"
Invoke-Native "gr_build finalize" { & $Python (Join-Path $Here "gr_build.py") finalize --info $InfoJson --dist $Dist }

Step "6/7 smoke test"
if ($SkipSmoke) { Write-Warning "smoke test skipped (-SkipSmoke)" }
else {
    $smokeArgs = @((Join-Path $Here "smoke_gate_runner.py"), "--dist", $Dist, "--work", (Join-Path $BuildDir "smoke"),
                   "--json", (Join-Path $BuildDir "smoke_result.json"))
    if ($FullTwin) { $smokeArgs += "--full-twin" }
    Invoke-Native "smoke test" { & $Python @smokeArgs }
}

Step "7/7 zip"
if ($NoZip) { Write-Host "skipped (-NoZip)" }
else { Invoke-Native "zip" { & $Python (Join-Path $Here "gr_build.py") zip --info $InfoJson --dist $Dist } }

Step "done"
$size = (Get-ChildItem $Dist -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("folder  {0}  {1:N1} MiB" -f $Dist, ($size / 1MB))
Write-Host ("total   {0:N0} s" -f ((Get-Date) - $t0).TotalSeconds)
