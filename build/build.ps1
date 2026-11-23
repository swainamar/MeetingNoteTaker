<#
.SYNOPSIS
  Builds the portable Meeting Note-Taker: MeetingNoteTaker.exe + a bundled Python runtime.

.DESCRIPTION
  1. Copies the Python 3.11 installed on this PC (standard library, Tk) into MeetingNoteTaker\runtime.
  2. Installs the packages from requirements.txt into that runtime.
  3. Compiles MeetingNoteTaker.exe (with the logo) using the C# compiler built into Windows.
  4. With -Package, creates dist\MeetingNoteTaker-<version>-win64.zip without any personal data.

  The result runs on any 64-bit Windows 10/11 PC: extract the ZIP and start MeetingNoteTaker.exe.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File build\build.ps1 -Package
#>
param(
    [switch]$Package,     # also create the ZIP in dist\
    [switch]$Gpu,         # add NVIDIA CUDA libraries (large; only for PCs with an NVIDIA card)
    [switch]$Clean,       # rebuild the runtime from scratch
    [switch]$SkipPackages # keep the packages already in the runtime (faster re-build)
)
$ErrorActionPreference = 'Stop'
$Version = '2.2'
$Root = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Root 'MeetingNoteTaker'
$Runtime = Join-Path $App 'runtime'
$Site = Join-Path $Runtime 'Lib\site-packages'

# ---------------------------------------------------------------- 1. Python runtime
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw 'Python 3.11 (64-bit) must be installed to build. https://www.python.org/downloads/windows/' }
$info = & $py -c "import sys,struct;print(sys.version_info[0],sys.version_info[1],struct.calcsize('P')*8);print(sys.base_prefix)"
$ver = $info[0].Split(' ')
if ($ver[0] -ne '3' -or $ver[1] -ne '11' -or $ver[2] -ne '64') { throw "Python 3.11 64-bit is required (found $($info[0]))." }
$Base = $info[1].Trim()
Write-Host "Using Python at $Base"

if ($Clean -and (Test-Path $Runtime)) { Remove-Item -Recurse -Force $Runtime }
New-Item -ItemType Directory -Force $Runtime | Out-Null
# robocopy skips unchanged files, so this also works while the app is running.
robocopy $Base $Runtime python311.dll python3.dll python.exe pythonw.exe vcruntime140.dll vcruntime140_1.dll LICENSE.txt /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the Python DLLs failed (robocopy $LASTEXITCODE)" }
foreach ($d in 'DLLs', 'Lib', 'tcl') {
    robocopy (Join-Path $Base $d) (Join-Path $Runtime $d) /E /XD site-packages test tests idlelib __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying $d failed (robocopy $LASTEXITCODE)" }
}
# ._pth beside the DLL: only these folders are used, the PC's Python settings are ignored.
@('.', 'Lib', 'DLLs', 'Lib\site-packages', '..', 'import site') |
    Set-Content -Encoding ascii (Join-Path $Runtime 'python311._pth')

# ---------------------------------------------------------------- 2. Packages
New-Item -ItemType Directory -Force $Site | Out-Null
if (-not $SkipPackages) {
    Write-Host 'Installing packages into the runtime (first time: a few minutes)...'
    & $py -m pip install --disable-pip-version-check --no-warn-script-location --upgrade `
        --target $Site -r (Join-Path $App 'requirements.txt') pip
    if ($LASTEXITCODE) { throw 'pip install failed' }
}
if ($Gpu -and -not $SkipPackages) {
    & $py -m pip install --disable-pip-version-check --no-warn-script-location --upgrade `
        --target $Site nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
    if ($LASTEXITCODE) { throw 'pip install (GPU) failed' }
}
Get-ChildItem $Runtime -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
# pip's command-line launchers point at the build PC's Python (and its user folder); the app
# never uses them, so they are removed.
if (Test-Path (Join-Path $Site 'bin')) { Remove-Item -Recurse -Force (Join-Path $Site 'bin') }

# ---------------------------------------------------------------- 3. MeetingNoteTaker.exe
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "C# compiler not found: $csc" }
$exe = Join-Path $Root 'MeetingNoteTaker.exe'
$out = Join-Path $PSScriptRoot 'out'
New-Item -ItemType Directory -Force $out | Out-Null
$built = Join-Path $out 'MeetingNoteTaker.exe'
& $csc /nologo /target:winexe /platform:x64 /optimize+ /win32icon:"$App\assets\logo.ico" `
    /reference:System.Windows.Forms.dll /out:"$built" (Join-Path $PSScriptRoot 'launcher.cs')
if ($LASTEXITCODE) { throw 'Compiling MeetingNoteTaker.exe failed' }
try { Copy-Item $built $exe -Force -ErrorAction Stop }
catch { Write-Warning 'MeetingNoteTaker.exe is running, so it was not replaced. Close the app and build again to update it.' }

# Quick self-test: the bundled runtime starts and can import the app's packages.
# (MeetingNoteTaker.exe has no console, so the result is written to a file.)
$probe = Join-Path $env:TEMP 'mnt_selftest.txt'
Remove-Item $probe -ErrorAction SilentlyContinue
$code = "import sys, tkinter, numpy, faster_whisper, sherpa_onnx, soundcard; open(r'$probe', 'w').write('OK ' + sys.prefix)"
Start-Process -FilePath $exe -ArgumentList '-c', ('"' + $code + '"') -Wait
if (-not (Test-Path $probe)) { throw 'Self-test failed: the bundled runtime did not start' }
Write-Host ("Self-test: " + (Get-Content $probe))

$size = (Get-ChildItem $Runtime -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built {0} (runtime {1:N0} MB)" -f $exe, $size)

# ---------------------------------------------------------------- 4. ZIP (no personal data)
if ($Package) {
    $dist = Join-Path $Root 'dist'
    $stage = Join-Path $dist 'Meeting Note-Taker'
    if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
    New-Item -ItemType Directory -Force $stage | Out-Null
    Copy-Item $built, (Join-Path $Root 'LICENSE') $stage
    # Personal data and downloaded models are excluded by full path (a bare "models" would also
    # drop library folders of that name). GPU libraries are left out: they are an optional add-on.
    robocopy $App (Join-Path $stage 'MeetingNoteTaker') /E `
        /XD (Join-Path $App 'meetings') (Join-Path $App 'models') (Join-Path $App 'voices') `
            __pycache__ (Join-Path $Site 'nvidia') nvidia_cublas_* nvidia_cuda_* nvidia_cudnn_* `
        /XF (Join-Path $App 'notetaker_settings.json') (Join-Path $App 'voice_prompt_settings.json') `
            (Join-Path $App 'notetaker.log') *.bak* .installed .shortcuts_created *.pyc `
        /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Staging failed (robocopy $LASTEXITCODE)" }
    @"
Meeting Note-Taker $Version - portable

1. Start MeetingNoteTaker.exe. Nothing needs to be installed.
2. To keep it handy: right-click MeetingNoteTaker.exe and choose
   "Pin to taskbar" or "Show more options > Send to > Desktop (create shortcut)".

Keep the MeetingNoteTaker folder next to MeetingNoteTaker.exe.
Your recordings are saved in MeetingNoteTaker\meetings.
Documentation: https://github.com/swainamar/MeetingNoteTaker
"@ | Set-Content -Encoding utf8 (Join-Path $stage 'README.txt')
    $zip = Join-Path $dist "MeetingNoteTaker-$Version-win64.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, 'Optimal', $true)
    Write-Host ("Package: {0} ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
}
