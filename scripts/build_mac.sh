#!/usr/bin/env bash
# Build DocWizard.app for macOS (Apple Silicon). Result: dist/DocWizard.app
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --extra desktop --group build
uv run pyinstaller packaging/docwizard.spec --noconfirm --clean
echo "Klaar: dist/DocWizard.app  (eerste keer openen: rechtsklik → Open, zolang de app niet ondertekend is)"
