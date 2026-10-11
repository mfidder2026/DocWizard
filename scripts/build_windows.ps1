# Build DocWizard for Windows 10/11. Result: dist\DocWizard\DocWizard.exe
# Vereist: uv (https://docs.astral.sh/uv/). WebView2 zit standaard in Windows 10/11.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
uv sync --extra desktop --group build
uv run pyinstaller packaging/docwizard.spec --noconfirm --clean
Write-Host "Klaar: dist\DocWizard\DocWizard.exe"
