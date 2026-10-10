<#
.SYNOPSIS
  Build the Bird Bend Stand Windows distribution (PyInstaller one-folder) + zip (+ optional Inno Setup installer).

.DESCRIPTION
  Owner: Implementer B (SW_design §24, 03_SW/packaging/README.md). Windows PowerShell 5.1 compatible.
  Steps: (1) check the venv interpreter and every installed package against the hash-pinned lock
  (requirements-build.lock.txt, direct + transitive, SWR-27),
  (2) stamp the version (pyproject version + git hash) into build\build_info.json, (3) PyInstaller with
  packaging\BirdBendStand.spec -> 03_SW\dist\BirdBendStand-<ver>\, (4) BUILD_INFO.txt, (5) smoke test of the
  built exe (packaging\smoke_dist.py: --sim GUI offscreen, --headless --sim, D-06 guard), (6) zip,
  (7) Inno Setup installer if ISCC.exe is installed and -Installer is given.
  No hardware is touched: the smoke runs use the in-process simulator and the frozen-exe D-06 guard.
  Implements: SW-PLT-001

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File 03_SW\packaging\build_dist.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File 03_SW\packaging\build_dist.ps1 -InstallDeps -Installer
#>
[CmdletBinding()]
param(
    [string]$Python = "",            # default: <repo>\.venv\Scripts\python.exe
    [string]$BuildDir = "",          # work files (default 03_SW\build; use a private folder for parallel builds)
    [string]$DistDir = "",           # output (default 03_SW\dist)
    [switch]$InstallDeps,            # pip install --require-hashes -r requirements-build.lock.txt first
    [switch]$AllowEnvMismatch,       # build even if installed versions differ from the pins (not reproducible)
    [switch]$SkipSmoke,
    [switch]$NativeGuiSmoke,         # smoke GUI runs on the real display (a window appears briefly)
    [switch]$NoZip,
    [switch]$Installer               # also build the Inno Setup installer (needs Inno Setup 6, ISCC.exe)
)
$ErrorActionPreference = "Stop"
$t0 = Get-Date
$Pack = $PSScriptRoot
$SW = Split-Path $Pack -Parent
$Repo = Split-Path $SW -Parent
if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\python.exe" }
if (-not $BuildDir) { $BuildDir = Join-Path $SW "build" }
if (-not $DistDir) { $DistDir = Join-Path $SW "dist" }
$BuildDir = [IO.Path]::GetFullPath($BuildDir)
$DistDir = [IO.Path]::GetFullPath($DistDir)
$WorkDir = Join-Path $BuildDir "pyinstaller"
$InfoJson = Join-Path $BuildDir "build_info.json"

function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Invoke-Native([string]$what, [scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"      # PS 5.1: native stderr must not become a terminating error
    try { & $cmd } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

Step "1/7 build environment"
if (-not (Test-Path $Python)) { throw "Python not found: $Python (create the project venv, see 03_SW\packaging\README.md)" }
$pyver = (& $Python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
Write-Host "python $pyver  ($Python)"
if (-not $pyver.StartsWith("3.14.")) { throw "Python 3.14 required (D-33 i), found $pyver" }
if ($InstallDeps) {
    Invoke-Native "pip install" {
        & $Python -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $SW "requirements-build.lock.txt")
    }
}
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $Python (Join-Path $Pack "buildinfo.py") check-env --req (Join-Path $SW "requirements-build.lock.txt")
$envRc = $LASTEXITCODE; $ErrorActionPreference = $prev
if ($envRc -ne 0) {
    if ($AllowEnvMismatch) { Write-Warning "installed versions differ from requirements-build.lock.txt (-AllowEnvMismatch)" }
    else { throw "installed versions differ from requirements-build.lock.txt; run with -InstallDeps (or -AllowEnvMismatch)" }
}

Step "2/7 version"
New-Item -ItemType Directory -Force $BuildDir | Out-Null
Invoke-Native "buildinfo info" { & $Python (Join-Path $Pack "buildinfo.py") info --out $InfoJson | Out-Null }
$info = Get-Content $InfoJson -Raw -Encoding UTF8 | ConvertFrom-Json
$Target = Join-Path $DistDir $info.dist_name
Write-Host "version $($info.version)  ->  $Target"
if ($info.dirty) { Write-Warning "packaged sources differ from git HEAD: the version is marked .dirty" }

Step "3/7 PyInstaller"
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
if (Test-Path "$Target.zip") { Remove-Item -Force "$Target.zip" }
$env:BBS_BUILD_INFO = $InfoJson
$env:SOURCE_DATE_EPOCH = [string]$info.commit_time
$tb = Get-Date
try {
    Invoke-Native "PyInstaller" {
        & $Python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $DistDir --workpath $WorkDir `
            (Join-Path $Pack "BirdBendStand.spec")
    }
} finally {
    Remove-Item Env:\BBS_BUILD_INFO -ErrorAction SilentlyContinue
    Remove-Item Env:\SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue
}
$buildSec = [math]::Round(((Get-Date) - $tb).TotalSeconds, 1)
if (-not (Test-Path (Join-Path $Target "BirdBendStand.exe"))) { throw "PyInstaller produced no $Target\BirdBendStand.exe" }
Write-Host "PyInstaller done in $buildSec s"

Step "4/7 BUILD_INFO.txt"
Invoke-Native "buildinfo finalize" { & $Python (Join-Path $Pack "buildinfo.py") finalize --info $InfoJson --dist $Target }

Step "5/7 smoke test"
if ($SkipSmoke) { Write-Warning "smoke test skipped (-SkipSmoke)" }
else {
    $smokeArgs = @((Join-Path $Pack "smoke_dist.py"), "--dist", $Target, "--json", (Join-Path $BuildDir "smoke_result.json"),
                   "--work", (Join-Path $BuildDir "smoke"))
    if ($NativeGuiSmoke) { $smokeArgs += "--native-gui" }
    Invoke-Native "smoke test" { & $Python @smokeArgs }
}

Step "6/7 zip"
if ($NoZip) { Write-Host "skipped (-NoZip)" }
else { Invoke-Native "zip" { & $Python (Join-Path $Pack "buildinfo.py") zip --info $InfoJson --dist $Target } }

Step "7/7 installer (Inno Setup)"
$iscc = $null
$cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($cmd) { $iscc = $cmd.Source }
foreach ($cand in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
    if (-not $iscc -and $cand -and (Test-Path $cand)) { $iscc = $cand }
}
if (-not $Installer) { Write-Host "skipped (use -Installer; ISCC $(if ($iscc) { 'found' } else { 'not installed' }))" }
elseif (-not $iscc) { Write-Warning "Inno Setup 6 (ISCC.exe) not installed - installer not built; the zip is the deliverable" }
else {
    Invoke-Native "ISCC" {
        & $iscc "/DAppVersion=$($info.version)" "/DAppVersionTuple=$($info.file_version)" "/DDistDir=$Target" `
            "/DOutputDir=$DistDir" "/DOutputBase=$($info.dist_name)-setup" (Join-Path $Pack "BirdBendStand.iss")
    }
}

Step "done"
$size = (Get-ChildItem $Target -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("folder  {0}  {1:N1} MiB" -f $Target, ($size / 1MB))
if (Test-Path "$Target.zip") { Write-Host ("zip     {0}.zip  {1:N1} MiB" -f $Target, ((Get-Item "$Target.zip").Length / 1MB)) }
$setup = Join-Path $DistDir "$($info.dist_name)-setup.exe"
if (Test-Path $setup) { Write-Host ("setup   {0}  {1:N1} MiB" -f $setup, ((Get-Item $setup).Length / 1MB)) }
Write-Host ("total   {0:N0} s" -f ((Get-Date) - $t0).TotalSeconds)
