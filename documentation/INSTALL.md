# Installation Guide

## Distribution Packages

This project provides pre-built distribution packages for easy installation across different platforms.

| File | Description |
|------|-------------|
| `dist/claude_harness-0.1.1-py3-none-any.whl` | Wheel package (pre-built) |
| `dist/claude_harness-0.1.1.tar.gz` | Source tarball (for PyPI) |

---

## Installation Methods by System

### macOS (Homebrew)

```bash
# Install directly from the formula
brew install --verbose Formula/harness.rb
```

Or, if you publish the `homebrew-tap/` directory to a GitHub repo:

```bash
brew tap user/homebrew-harness
brew install harness
```

### Linux (Generic)

**Option 1: Automated installer script**
```bash
chmod +x linux-install/install-linux.sh
./linux-install/install-linux.sh
```

The script auto-detects: Ubuntu/Debian, RHEL/CentOS/Fedora, Arch Linux

**Option 2: Direct pip install**
```bash
pip install dist/claude_harness-0.1.1-py3-none-any.whl
```

### Windows

```bash
pip install dist/claude_harness-0.1.1-py3-none-any.whl
```

### From PyPI (when published)

```bash
pip install claude-harness
```

### Development / Editable Install

```bash
pip install -e .
```

---

## Verify Installation

After installation, verify with:

```bash
harness --version    # Should show: harness, version 0.1.0
harness --help       # Should show CLI commands
```

---

## Building from Source

To rebuild the distribution packages:

```bash
pip install build
python -m build
```

This creates:
- `dist/claude_harness-0.1.1-py3-none-any.whl`
- `dist/claude_harness-0.1.1.tar.gz`

---

## Publishing to PyPI

```bash
pip install twine
twine upload dist/*
```

After publishing, users can install via:
```bash
pip install claude-harness
```
