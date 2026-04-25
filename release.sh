#!/usr/bin/env bash
# release.sh — Build, validate, bump version, and package for release
# Usage: ./release.sh <17|18|19> <patch|minor|major>

set -euo pipefail

VERSION="${1:-}"
BUMP="${2:-}"

if [[ -z "$VERSION" || -z "$BUMP" ]]; then
    echo "Usage: $0 <17|18|19> <patch|minor|major>"
    echo "  e.g. $0 18 patch"
    exit 1
fi
if [[ "$VERSION" != "17" && "$VERSION" != "18" && "$VERSION" != "19" ]]; then
    echo "Error: VERSION must be 17, 18, or 19. Got: $VERSION"
    exit 1
fi
if [[ "$BUMP" != "patch" && "$BUMP" != "minor" && "$BUMP" != "major" ]]; then
    echo "Error: BUMP must be patch, minor, or major. Got: $BUMP"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$SCRIPT_DIR/odoo-uva-connector"
MANIFEST="$MODULE_DIR/__manifest_${VERSION}__.py"

if [[ ! -f "$MANIFEST" ]]; then
    echo "Error: manifest not found at $MANIFEST"
    exit 1
fi

# Step 1: Run deploy.sh (build + validate)
echo "==> Step 1: Running deploy.sh for Odoo $VERSION"
bash "$SCRIPT_DIR/deploy.sh" "$VERSION"

# Step 2: Read current version
CURRENT=$(grep "'version'" "$MANIFEST" | head -1 | sed "s/.*'\([^']*\)'.*/\1/")
if [[ -z "$CURRENT" ]]; then
    echo "Error: could not read version from $MANIFEST"
    exit 1
fi
echo "==> Step 2: Current version: $CURRENT"

# Step 3: Bump version
# Format: X.0.A.B.C  (odoo_ver.0.major.minor.patch)
IFS='.' read -r V0 V1 MAJOR MINOR PATCH <<< "$CURRENT"
case "$BUMP" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
esac
NEW_VERSION="${V0}.${V1}.${MAJOR}.${MINOR}.${PATCH}"
echo "==> Step 3: New version: $NEW_VERSION"

# Step 4: Update manifest
sed -i "s/'version':.*/'version': '${NEW_VERSION}',/" "$MANIFEST"
echo "==> Step 4: Updated $MANIFEST"

# Also update the generated __manifest__.py
bash "$MODULE_DIR/build.sh" "$VERSION"

# Step 5: Create zip package
ZIPNAME="odoo_uva_connector-${NEW_VERSION}.zip"
(cd "$SCRIPT_DIR" && zip -r "$ZIPNAME" odoo-uva-connector/ \
    --exclude '*.pyc' --exclude '*/__pycache__/*' --exclude '*/.git/*' \
    --exclude '*/requirements-dev.txt' --exclude '*/doc/*' -q)
echo "==> Step 5: Created $ZIPNAME"

# Summary
echo ""
echo "=== Release Summary ==="
echo "  Odoo version:    $VERSION"
echo "  Previous version: $CURRENT"
echo "  New version:      $NEW_VERSION"
echo "  Bump type:        $BUMP"
echo "  Package:          $ZIPNAME"
echo "  Status:           ✅ Release ready"
echo ""
echo "Next steps:"
echo "  1. git add -A && git commit -m 'Release $NEW_VERSION'"
echo "  2. git tag v$NEW_VERSION"
echo "  3. Upload $ZIPNAME to Odoo App Store"
