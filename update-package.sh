#!/bin/bash
set -e

# ============================================================
# Claude Harness Package Update Script
#
# This script updates version numbers, rebuilds distribution
# packages, and provides SHA256 for Homebrew formula updates.
# ============================================================

echo "=== Claude Harness Package Updater ==="
echo ""

# Get current version from pyproject.toml
CURRENT_VERSION=$(grep -E '^version = "[0-9]+\.[0-9]+\.[0-9]+"' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo "Current version: $CURRENT_VERSION"
echo ""

# Prompt for new version
read -p "Enter new version (e.g., 1.0.1): " NEW_VERSION

# Validate version format
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Invalid version format. Use semver (e.g., 1.0.1)"
    exit 1
fi

echo ""
echo "Updating version: $CURRENT_VERSION → $NEW_VERSION"
echo ""

# Update pyproject.toml
sed -i.bak "s/^version = \"$CURRENT_VERSION\"/version = \"$NEW_VERSION\"/" pyproject.toml
rm -f pyproject.toml.bak

# Update Homebrew formula
HOMEBREW_FILE="homebrew-tap/Formula/harness.rb"

# Get SHA256 of the source tarball (we'll build this first)
echo "Building distribution packages..."

# Clean old builds
rm -rf dist/ build/ *.egg-info

# Install build if needed
pip install build -q 2>/dev/null || true

# Build packages
python -m build

echo ""
echo "Packages built successfully!"
echo ""

# Calculate SHA256 of the source tarball
TARBALL="dist/claude_harness-${NEW_VERSION}.tar.gz"
SHA256=$(sha256sum "$TARBALL" | cut -d' ' -f1)

echo "=========================================="
echo "=== Homebrew Formula Update Required ==="
echo "=========================================="
echo ""
echo "Update $HOMEBREW_FILE with:"
echo ""
echo "  version \"$NEW_VERSION\""
echo "  url \"https://files.pythonhosted.org/packages/claude-harness-$NEW_VERSION.tar.gz\""
echo "  sha256 \"$SHA256\""
echo ""
echo "Or run this command to update automatically:"
echo "  sed -i '' \\"
echo "    -e 's/version \"[^\"]*\"/version \"$NEW_VERSION\"/' \\"
echo "    -e 's|https://files.pythonhosted.org/packages/claude-harness-[^/]*.tar.gz|https://files.pythonhosted.org/packages/claude-harness-$NEW_VERSION.tar.gz|' \\"
echo "    -e 's/sha256 \"[^\"]*\"/sha256 \"$SHA256\"/' \\"
echo "    $HOMEBREW_FILE"
echo ""

# Auto-update Homebrew formula
sed -i '' \
    -e "s/version \"[^\"]*\"/version \"$NEW_VERSION\"/" \
    -e "s|https://files.pythonhosted.org/packages/claude-harness-[^/]*.tar.gz|https://files.pythonhosted.org/packages/claude-harness-$NEW_VERSION.tar.gz|" \
    -e "s/sha256 \"[^\"]*\"/sha256 \"$SHA256\"/" \
    "$HOMEBREW_FILE"

echo "Homebrew formula updated!"
echo ""

echo "=========================================="
echo "=== Next Steps ==="
echo "=========================================="
echo ""
echo "1. Review changes:"
echo "   git diff pyproject.toml $HOMEBREW_FILE"
echo ""
echo "2. Commit changes:"
echo "   git add pyproject.toml $HOMEBREW_FILE"
echo "   git commit -m \"chore: bump version to $NEW_VERSION\""
echo ""
echo "3. Push to GitHub:"
echo "   git push"
echo ""
echo "4. Publish to PyPI (if ready):"
echo "   pip install twine"
echo "   twine upload dist/*"
echo ""
echo "5. Tag the release:"
echo "   git tag v$NEW_VERSION"
echo "   git push origin v$NEW_VERSION"
echo ""
