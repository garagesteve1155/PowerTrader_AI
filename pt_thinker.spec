# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PowerTrader AI Thinker.

This specification defines the build configuration for the AI signal processor
subprocess (pt_thinker.py).

The Thinker is responsible for:
- Fetching live price data from exchanges (Kucoin)
- Pattern matching against trained AI models
- Generating LONG/SHORT trading signals
- Writing signals to data files for pt_trader to consume

Build modes:
    - onedir: Creates a directory with executable and dependencies (RECOMMENDED)
    - Console: Shows console output for logging (background process)

Usage:
    pyinstaller pt_thinker.spec

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

APP_NAME = 'pt_thinker'
ENTRY_POINT = 'pt_thinker.py'
VERSION = '1.0.0'

# Console application (shows logging output)
CONSOLE_MODE = True

# ============================================================================
# Data Files Collection
# ============================================================================

datas = []

# Certificate bundle for HTTPS API calls
datas += collect_data_files('certifi')

# ============================================================================
# Hidden Imports
# ============================================================================

hiddenimports = []

# Kucoin exchange API
hiddenimports += [
    'kucoin',
    'kucoin.client',
    'kucoin.client.market',
    'kucoin.base_request',
]

# Cryptography for API security (PyNaCl)
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

# System utilities
hiddenimports += [
    'psutil',
    'logging',
    'logging.handlers',
]

# Data handling
hiddenimports += [
    'json',
    'uuid',
    'hashlib',
    'hmac',
    'base64',
]

# ============================================================================
# Exclusions
# ============================================================================

excludes = [
    # GUI frameworks (not needed for background process)
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
    console=CONSOLE_MODE,  # Show console for logging
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
