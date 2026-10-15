# Builds dist\AdaWork-Setup.exe: fetches the bundled programs, freezes the app with
# PyInstaller and wraps it with Inno Setup (winget install JRSoftware.InnoSetup --scope user).
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe $arguments failed ($LASTEXITCODE)" }
}

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup --scope user" }

Run uv @("sync", "--locked")
Run uv @("run", "python", "tools/fetch_llama.py")
Run uv @("run", "--with", "zstandard", "python", "tools/fetch_motis.py")
Run uv @("run", "python", "tools/fetch_licenses.py")
New-Item -ItemType Directory -Force build | Out-Null
Run uv @("run", "python", "-c",
    "from pathlib import Path; from adawork.desktop.window import write_icon_file; write_icon_file(Path('build/adawork.ico'))")
Run uv @("run", "python", "-m", "PyInstaller", "packaging/adawork.spec", "--noconfirm", "--clean",
    "--distpath", "dist", "--workpath", "build/pyinstaller")
$version = (& uv run python -c "import adawork; print(adawork.__version__)").Trim()
Run $iscc @("/Q", "/DAppVersion=$version", "packaging\installer.iss")
# A clean Windows 11 with 8 GB like the minimum machine (Windows Sandbox, PLAN §10): open
# dist\sandbox.wsb, then run the installer from the window that opens.
@"
<Configuration>
  <MemoryInMB>8192</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$root\dist</HostFolder><SandboxFolder>C:\Setup</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>explorer.exe C:\Setup</Command></LogonCommand>
</Configuration>
"@ | Set-Content dist\sandbox.wsb -Encoding utf8
Get-Item dist\AdaWork-Setup.exe | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB) } }
