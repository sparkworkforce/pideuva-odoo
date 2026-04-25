#!/usr/bin/env bash
# deploy.sh — Build, validate, and package odoo-uva-connector
# Usage: ./deploy.sh 17|18|19

set -euo pipefail

cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "❌ Deploy failed (exit code $exit_code)"
    fi
    exit $exit_code
}
trap cleanup EXIT

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <17|18|19>"
    exit 1
fi
if [[ "$VERSION" != "17" && "$VERSION" != "18" && "$VERSION" != "19" ]]; then
    echo "Error: VERSION must be 17, 18, or 19. Got: $VERSION"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$SCRIPT_DIR/odoo-uva-connector"

if [[ ! -d "$MODULE_DIR" ]]; then
    echo "Error: module directory not found at $MODULE_DIR"
    exit 1
fi

# Step 1: Run build.sh
echo "==> Step 1: Running build.sh for Odoo $VERSION"
bash "$MODULE_DIR/build.sh" "$VERSION"

# Step 2: Python syntax check
echo "==> Step 2: Python syntax check"
PY_ERRORS=0
while IFS= read -r -d '' pyfile; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        echo "  FAIL: $pyfile"
        PY_ERRORS=$((PY_ERRORS + 1))
    fi
done < <(find "$MODULE_DIR" -name "*.py" -not -path "*/__pycache__/*" -print0)
if [[ $PY_ERRORS -gt 0 ]]; then
    echo "Error: $PY_ERRORS Python file(s) have syntax errors"
    exit 1
fi
PY_COUNT=$(find "$MODULE_DIR" -name "*.py" -not -path "*/__pycache__/*" | wc -l)
echo "  ✓ $PY_COUNT Python files OK"

# Step 3: XML validation
echo "==> Step 3: XML validation"
XML_ERRORS=0
XML_COUNT=0
while IFS= read -r -d '' xmlfile; do
    XML_COUNT=$((XML_COUNT + 1))
    if ! python3 -c "import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])" "$xmlfile" 2>/dev/null; then
        echo "  FAIL: $xmlfile"
        XML_ERRORS=$((XML_ERRORS + 1))
    fi
done < <(find "$MODULE_DIR" -name "*.xml" -not -path "*/.git/*" -print0)
if [[ $XML_ERRORS -gt 0 ]]; then
    echo "Error: $XML_ERRORS XML file(s) have syntax errors"
    exit 1
fi
echo "  ✓ $XML_COUNT XML files OK"

# Summary
echo ""
echo "=== Deploy Summary ==="
echo "  Odoo version: $VERSION"
echo "  Python files: $PY_COUNT"
echo "  XML files:    $XML_COUNT"
echo "  Status:       ✅ All checks passed"
