#!/usr/bin/env bash
# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
#
# build.sh — Packaging script for odoo-uva-connector
#
# Usage:
#   ./build.sh 17    # Package for Odoo 17
#   ./build.sh 18    # Package for Odoo 18
#   ./build.sh 19    # Package for Odoo 19
#
# What it does:
#   1. Copies __manifest_{VERSION}__.py to __manifest__.py
#   2. Verifies OPL-1 headers on all Python files
#   3. Reports any files missing the license header

set -euo pipefail

VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <17|18|19>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Validate version
if [[ "$VERSION" != "17" && "$VERSION" != "18" && "$VERSION" != "19" ]]; then
    echo "Error: VERSION must be 17, 18, or 19. Got: $VERSION"
    exit 1
fi

MANIFEST_SRC="__manifest_${VERSION}__.py"

if [[ ! -f "$MANIFEST_SRC" ]]; then
    echo "Error: $MANIFEST_SRC not found in $(pwd)"
    exit 1
fi

echo "==> Copying $MANIFEST_SRC → __manifest__.py"
cp "$MANIFEST_SRC" "__manifest__.py"

echo "==> Verifying OPL-1 license headers on Python files..."
MISSING=()
while IFS= read -r -d '' pyfile; do
    if ! grep -q "OPL-1" "$pyfile"; then
        MISSING+=("$pyfile")
    fi
done < <(find . -name "*.py" -not -path "./.git/*" -print0)

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "WARNING: The following Python files are missing OPL-1 license headers:"
    for f in "${MISSING[@]}"; do
        echo "  $f"
    done
else
    echo "==> All Python files have OPL-1 headers."
fi

echo ""
echo "==> Build complete for Odoo $VERSION"
echo "    Manifest: __manifest__.py (version $(grep "'version'" __manifest__.py | head -1 | tr -d "' ," | cut -d: -f2))"
echo ""
echo "To package for App Store, zip the module directory:"
echo "  cd .. && zip -r odoo_uva_connector_v${VERSION}.zip odoo-uva-connector/ --exclude '*.pyc' --exclude '__pycache__/*' --exclude '.git/*'"
