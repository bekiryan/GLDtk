# Build the GLDtk Windows installer (unsigned, x64). See README "Build the Windows app".
#   1. PyInstaller one-folder build of server.py  -> packaging\dist\gldtk-server
#   2. Haxe release builds (renderer.hxml, main.hxml)
#   3. electron-builder --win nsis --x64          -> ldtk-src\app\redist\
# Needs Python 3.11+ (a .venv in the repo, or $env:PYTHON), Haxe 4.3 with the haxelibs, Node 18+.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Invoke-Checked([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($ArgList -join ' ') failed ($LASTEXITCODE)" }
}

Write-Host "==> 1/3 backend bundle (PyInstaller)"
$Py = $env:PYTHON
if (-not $Py) { $Py = Join-Path $Root ".venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { $Py = "python" }
Invoke-Checked $Py @("-m", "pip", "install", "--quiet", "pyinstaller")
Push-Location (Join-Path $Root "packaging")
try {
    Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
    Invoke-Checked $Py @("-m", "PyInstaller", "--noconfirm", "--distpath", "dist", "--workpath", "build", "gldtk-server.spec")
} finally { Pop-Location }
Invoke-Checked (Join-Path $Root "packaging\dist\gldtk-server\gldtk-server.exe") @("--check")

Write-Host "==> 2/3 Haxe release builds"
$Haxe = $env:HAXE
if (-not $Haxe) { $Haxe = "haxe" }
Push-Location (Join-Path $Root "ldtk-src")
try {
    Invoke-Checked $Haxe @("renderer.hxml")
    Invoke-Checked $Haxe @("main.hxml")
} finally { Pop-Location }

Write-Host "==> 3/3 electron-builder"
Push-Location (Join-Path $Root "ldtk-src\app")
try {
    if (-not (Test-Path node_modules)) { Invoke-Checked "npm" @("install") }
    Remove-Item -Recurse -Force redist -ErrorAction SilentlyContinue
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    Invoke-Checked "npx" @("electron-builder", "build", "--win", "nsis", "--x64", "--publish", "never")
    $Exe = Get-ChildItem redist\*.exe | Select-Object -First 1
    Write-Host "Built: $($Exe.FullName)"
    Write-Host "App:   $(Join-Path $PWD 'redist\win-unpacked\GLDtk.exe')"
} finally { Pop-Location }
