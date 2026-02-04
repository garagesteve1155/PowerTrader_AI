"""
PowerTrader AI - Utility Functions

This module provides utility functions for PowerTrader AI, with special focus
on enabling the application to run correctly in both development (script) and
production (PyInstaller bundled) environments.

Key functionality:
- Resource path resolution for bundled applications
- Subprocess management for multi-process architecture
- Environment detection (bundled vs script mode)

Author: PowerTrader AI Contributors
License: Apache 2.0
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


def is_bundled() -> bool:
    """
    Detect if the application is running as a PyInstaller bundle.

    Returns:
        True if running as bundled executable, False if running as script

    Example:
        >>> if is_bundled():
        ...     print("Running as executable")
        ... else:
        ...     print("Running as script")
    """
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_bundle_dir() -> Path:
    """
    Get the directory containing the bundled application.

    When bundled, this is the directory containing the executable.
    When not bundled, this is the directory containing this script.

    Returns:
        Path to the application directory

    Example:
        >>> bundle_dir = get_bundle_dir()
        >>> config_file = bundle_dir / 'config.json'
    """
    if is_bundled():
        # PyInstaller extracts to a temp folder and stores the path in _MEIPASS
        # But we want the actual application directory, not the temp extraction
        return Path(sys.executable).parent
    else:
        # Script mode - return directory containing this file
        return Path(__file__).parent


def get_resource_path(relative_path: str | Path) -> Path:
    """
    Get absolute path to a resource file.

    Works correctly in both development and bundled environments.
    Use this for accessing data files that are bundled with the application.

    Args:
        relative_path: Relative path to resource from application root

    Returns:
        Absolute path to the resource

    Raises:
        FileNotFoundError: If resource doesn't exist

    Example:
        >>> logo_path = get_resource_path('assets/logo.png')
        >>> with open(logo_path, 'rb') as f:
        ...     logo_data = f.read()
    """
    if is_bundled():
        # Use PyInstaller's temporary extraction folder
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # Script mode
        base_path = Path(__file__).parent

    resource_path = base_path / relative_path

    if not resource_path.exists():
        raise FileNotFoundError(f"Resource not found: {resource_path}")

    return resource_path


def get_sibling_executable_path(script_name: str) -> Path:
    """
    Get path to sibling executable/script for subprocess launching.

    This function handles the critical difference between bundled and script mode:
    - Bundled: Returns path to sibling executable (pt_thinker, pt_trader)
    - Script: Returns path to Python script (pt_thinker.py, pt_trader.py)

    Args:
        script_name: Name of script (with or without .py extension)

    Returns:
        Path to executable or script

    Raises:
        FileNotFoundError: If executable/script not found

    Example:
        >>> thinker_path = get_sibling_executable_path('pt_thinker.py')
        >>> # Returns: /path/to/pt_thinker (bundled) or /path/to/pt_thinker.py (script)
    """
    # Normalize script name (remove .py if present)
    script_base = script_name.replace('.py', '')

    if is_bundled():
        # Look for compiled executable (sibling to current executable)
        bundle_dir = get_bundle_dir()

        # Platform-specific executable name
        if sys.platform == 'win32':
            exe_name = f'{script_base}.exe'
        else:
            exe_name = script_base

        exe_path = bundle_dir / exe_name

        if not exe_path.exists():
            # On macOS, might be inside .app bundle
            if sys.platform == 'darwin':
                # Check if we're inside a .app bundle
                app_bundle_path = bundle_dir / f'{script_base}.app' / 'Contents' / 'MacOS' / script_base
                if app_bundle_path.exists():
                    return app_bundle_path

            raise FileNotFoundError(
                f"Sibling executable not found: {exe_path}\n"
                f"Expected to find '{exe_name}' in: {bundle_dir}"
            )

        return exe_path
    else:
        # Script mode - return path to .py file
        script_dir = Path(__file__).parent
        script_path = script_dir / f'{script_base}.py'

        if not script_path.exists():
            raise FileNotFoundError(
                f"Script not found: {script_path}\n"
                f"Expected to find '{script_base}.py' in: {script_dir}"
            )

        return script_path


def get_subprocess_command(script_name: str, args: Optional[List[str]] = None) -> List[str]:
    """
    Build complete subprocess command for launching sibling processes.

    This function returns the correct command format for both environments:
    - Bundled: ['./pt_thinker', arg1, arg2, ...]
    - Script: ['/path/to/python', '-u', '/path/to/pt_thinker.py', arg1, arg2, ...]

    Args:
        script_name: Name of script to launch (e.g., 'pt_thinker.py')
        args: Optional list of command-line arguments

    Returns:
        Complete command list ready for subprocess.Popen()

    Example:
        >>> cmd = get_subprocess_command('pt_thinker.py')
        >>> process = subprocess.Popen(cmd, cwd=working_dir)
    """
    if args is None:
        args = []

    executable_path = get_sibling_executable_path(script_name)

    if is_bundled():
        # Bundled: just run the executable directly
        cmd = [str(executable_path)] + args
    else:
        # Script: run with Python interpreter
        # -u flag ensures unbuffered output (important for real-time logging)
        cmd = [sys.executable, '-u', str(executable_path)] + args

    logger.debug(f"Subprocess command for '{script_name}': {cmd}")
    return cmd


def get_application_name() -> str:
    """
    Get the name of the current application.

    Returns:
        Application name (executable name in bundled mode, script name otherwise)

    Example:
        >>> app_name = get_application_name()
        >>> print(f"Running: {app_name}")
    """
    if is_bundled():
        return Path(sys.executable).stem
    else:
        return Path(sys.argv[0]).stem


def get_data_directory() -> Path:
    """
    Get directory for user data storage.

    This directory should be used for:
    - Configuration files (r_key.txt, r_secret.txt)
    - Coin data folders (BTC/, ETH/, etc.)
    - Training data
    - Any user-generated content

    In bundled mode, this is the directory containing the executable.
    In script mode, this is the script directory.

    Returns:
        Path to data directory

    Example:
        >>> data_dir = get_data_directory()
        >>> config_file = data_dir / 'r_key.txt'
        >>> btc_folder = data_dir / 'BTC'
    """
    return get_bundle_dir()


# Module initialization logging
if __name__ != '__main__':
    logger.debug(f"PowerTrader Utils initialized - Bundled: {is_bundled()}")
    logger.debug(f"Application directory: {get_bundle_dir()}")
    logger.debug(f"Data directory: {get_data_directory()}")


# For testing/debugging when run directly
if __name__ == '__main__':
    print(f"PowerTrader AI Utilities - Debug Information")
    print(f"=" * 60)
    print(f"Is bundled: {is_bundled()}")
    print(f"Application name: {get_application_name()}")
    print(f"Bundle directory: {get_bundle_dir()}")
    print(f"Data directory: {get_data_directory()}")
    print()

    print(f"Testing sibling executable resolution:")
    for script in ['pt_thinker.py', 'pt_trader.py', 'pt_trainer.py']:
        try:
            path = get_sibling_executable_path(script)
            print(f"  {script:20} -> {path}")
        except FileNotFoundError as e:
            print(f"  {script:20} -> NOT FOUND: {e}")

    print()
    print(f"Testing subprocess command generation:")
    for script in ['pt_thinker.py', 'pt_trader.py']:
        try:
            cmd = get_subprocess_command(script, ['arg1', 'arg2'])
            print(f"  {script:20} -> {' '.join(cmd)}")
        except FileNotFoundError as e:
            print(f"  {script:20} -> ERROR: {e}")
