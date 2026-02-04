# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PowerTrader AI Trader.

This specification defines the build configuration for the trading engine
subprocess (pt_trader.py).

The Trader is responsible for:
- Reading signals from pt_thinker
- Executing trades via Robinhood API
- Managing DCA (Dollar Cost Averaging) strategy
- Handling trailing profit margins
- Position and order management

Build modes:
    - onedir: Creates a directory with executable and dependencies (RECOMMENDED)
    - Console: Shows console output with colored logging

Usage:
    pyinstaller pt_trader.spec

Requirements:
    - Python 3.12+
    - PyInstaller 6.18.0+
    - All dependencies from requirements.txt installed

Author: PowerTrader AI Contributors
License: Apache 2.0
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import sys

# ============================================================================
# Configuration Constants
# ============================================================================

APP_NAME = 'pt_trader'
ENTRY_POINT = 'pt_trader.py'
VERSION = '1.0.0'

# Console application (shows colored logging output)
CONSOLE_MODE = True

# ============================================================================
# Data Files Collection
# ============================================================================

datas = []

# Certificate bundle for HTTPS API calls
datas += collect_data_files('certifi')

# Colorama data files (if any)
try:
    datas += collect_data_files('colorama')
except Exception:
    pass  # Colorama may not have data files

# ============================================================================
# Hidden Imports
# ============================================================================

hiddenimports = []

# Cryptography for Robinhood API authentication
hiddenimports += collect_submodules('cryptography.hazmat.primitives')
hiddenimports += [
    'cryptography',
    'cryptography.hazmat.primitives.asymmetric.ed25519',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
]

# PyNaCl for signing
hiddenimports += [
    'nacl',
    'nacl.signing',
    'nacl.encoding',
    'nacl.exceptions',
]

# Networking libraries
hiddenimports += [
    'requests',
    'urllib3',
    'certifi',
    'charset_normalizer',
]

# Terminal colors
hiddenimports += [
    'colorama',
    'colorama.ansi',
    'colorama.initialise',
]

# Data handling
hiddenimports += [
    'json',
    'uuid',
    'base64',
]

# ============================================================================
# Exclusions
# ============================================================================

excludes = [
    # GUI frameworks (not needed for trading engine)
    'tkinter',
    'matplotlib',
    'PIL',

    # Testing frameworks
    'pytest',
    'unittest',
    '_pytest',

    # Development tools
    'IPython',
    'jupyter',

    # Exchange APIs not used by trader
    'kucoin',
]

# ============================================================================
# Analysis Stage
# ============================================================================

a = Analysis(
    [ENTRY_POINT],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# ============================================================================
# PYZ Archive (Python ZIP)
# ============================================================================

pyz = PYZ(
    a.pure,
    cipher=None
)

# ============================================================================
# EXE Configuration (onedir mode)
# ============================================================================

exe = EXE(
    pyz,
    a.scripts,
    [],  # Empty for onedir mode
    exclude_binaries=True,  # Required for onedir mode
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE_MODE,  # Show console for colored logging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ============================================================================
# COLLECT Stage (onedir mode)
# ============================================================================

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
