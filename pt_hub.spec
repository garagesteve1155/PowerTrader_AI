# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PowerTrader AI Hub.

This specification defines the build configuration for creating a standalone
distributable application of the PowerTrader AI main GUI (pt_hub.py).

Build modes:
    - onedir: Creates a directory with executable and dependencies (RECOMMENDED)
    - Windowed: No console window (GUI-only application)
    - macOS .app bundle: Native macOS application bundle

Usage:
    pyinstaller pt_hub.spec

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

APP_NAME = 'PowerTrader_AI'
ENTRY_POINT = 'pt_hub.py'
VERSION = '1.0.0'

# Icon files (optional - set to None if not available)
ICON_WINDOWS = None  # 'assets/icon.ico'
ICON_MAC = None  # 'assets/icon.icns'

# macOS app bundle identifier
BUNDLE_ID = 'com.powertrader.ai'

# ============================================================================
# Data Files Collection
# ============================================================================

datas = []

# Matplotlib data files (fonts, configuration, etc.)
datas += collect_data_files('matplotlib')

# Certificate bundle for HTTPS requests
datas += collect_data_files('certifi')

# Future: Add application assets when available
# datas += [('assets', 'assets')]

# ============================================================================
# Hidden Imports
# ============================================================================

hiddenimports = []

# Matplotlib backends
hiddenimports += collect_submodules('matplotlib.backends')
hiddenimports += [
    'matplotlib.backends.backend_tkagg',
    'matplotlib.backends.backend_agg',
]

# Pillow (PIL) imports for matplotlib image support
hiddenimports += [
    'PIL',
    'PIL.Image',
    'PIL.ImageTk',
    'PIL._imagingtk',
    'PIL._tkinter_finder',
]

# Cryptography for API security
hiddenimports += collect_submodules('cryptography')
hiddenimports += [
    'cryptography.hazmat.bindings',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.primitives',
]

# Kucoin API (if used)
hiddenimports += [
    'kucoin',
    'kucoin.base_request',
]

# Networking libraries
hiddenimports += [
    'urllib3',
    'requests',
    'certifi',
    'charset_normalizer',
]

# Utilities
hiddenimports += [
    'psutil',
    'queue',
    'threading',
]

# ============================================================================
# Exclusions
# ============================================================================

excludes = [
    # Testing frameworks
    'pytest',
    'unittest',
    '_pytest',
    'nose',

    # Development tools
    'IPython',
    'jupyter',
    'notebook',

    # Unused matplotlib backends
    'matplotlib.backends.backend_qt5agg',
    'matplotlib.backends.backend_wx',
    'matplotlib.backends.backend_gtk3',
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
    [],  # Empty for onedir mode (dependencies in separate files)
    exclude_binaries=True,  # Required for onedir mode
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # Compress executables with UPX
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Windowed application (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_WINDOWS if sys.platform == 'win32' else None,
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

# ============================================================================
# macOS .app Bundle
# ============================================================================

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=f'{APP_NAME}.app',
        icon=ICON_MAC,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            'CFBundleName': APP_NAME,
            'CFBundleDisplayName': 'PowerTrader AI',
            'CFBundleVersion': VERSION,
            'CFBundleShortVersionString': VERSION,
            'CFBundleIdentifier': BUNDLE_ID,
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.15.0',
            'CFBundleDocumentTypes': [],
            'NSRequiresAquaSystemAppearance': False,  # Support dark mode
        },
    )
