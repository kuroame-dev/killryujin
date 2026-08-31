# Build killryujin.exe and, if Inno Setup is installed, killryujin-setup.exe.
# From this repo:  powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller `
  --noconfirm `
  --clean `
  --noconsole `
  --onefile `
  --name killryujin `
  --icon killryujin/icon.ico `
  --version-file tools/file_version_info.txt `
  --collect-binaries hid `
  --hidden-import PySide6.QtCore `
  --hidden-import PySide6.QtGui `
  --hidden-import PySide6.QtWidgets `
  --hidden-import killryujin `
  --hidden-import killryujin.__main__ `
  --hidden-import killryujin.gui `
  --hidden-import killryujin.device `
  --hidden-import killryujin.crate `
  --hidden-import killryujin.winusb_bulk `
  --collect-submodules killryujin `
  --add-data "killryujin/icon.png;killryujin" `
  --add-data "killryujin/icon.ico;killryujin" `
  tools\pyinstaller_entry.py

Write-Host "Built: dist\killryujin.exe"

function Find-Iscc {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $guess = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $guess) {
        return $guess
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "Inno Setup 6 not found. Installer skipped. Exe is at dist\killryujin.exe."
    exit 0
}

& $iscc tools\installer.iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
Write-Host "Built: dist\killryujin-setup.exe"
