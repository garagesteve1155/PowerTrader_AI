# Code Standards - Senior Level Python Development

## Overview

All code contributions must meet senior-level quality standards (15+ years industry experience equivalent). This includes proper architecture, type safety, error handling, testing, and documentation.

## Python Style Guide

### PEP 8 Compliance
- Line length: 100 characters (not 79, modern standard)
- Use 4 spaces for indentation (never tabs)
- Two blank lines between top-level functions and classes
- One blank line between methods in a class
- Imports organized: stdlib, third-party, local

### Type Hints (Required)
All functions must have type hints for parameters and return values.

**Bad:**
```python
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(__file__), relative_path)
```

**Good:**
```python
from pathlib import Path
from typing import Union

def get_resource_path(relative_path: Union[str, Path]) -> Path:
    """
    Get absolute path to resource, works for dev and PyInstaller bundled apps.

    Args:
        relative_path: Relative path to resource file

    Returns:
        Absolute path to resource

    Raises:
        FileNotFoundError: If resource doesn't exist
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent

    resource_path = base_path / relative_path

    if not resource_path.exists():
        raise FileNotFoundError(f"Resource not found: {resource_path}")

    return resource_path
```

### Docstrings (Required)
Use Google-style docstrings for all public functions and classes.

```python
def calculate_neural_levels(
    candle_data: List[Dict[str, float]],
    timeframes: List[str]
) -> Dict[str, List[float]]:
    """
    Calculate neural price levels across multiple timeframes.

    Processes historical candle data to generate predicted high/low prices
    for each specified timeframe using weighted pattern matching.

    Args:
        candle_data: List of OHLCV candles with timestamps
        timeframes: List of timeframe strings (e.g., ['1h', '4h', '1d'])

    Returns:
        Dictionary mapping timeframe to list of [low, high] predictions

    Raises:
        ValueError: If candle_data is empty or timeframes invalid

    Example:
        >>> data = [{'open': 50000, 'high': 51000, 'low': 49000, 'close': 50500}]
        >>> levels = calculate_neural_levels(data, ['1h', '4h'])
        >>> levels['1h']
        [49500.0, 51500.0]
    """
    if not candle_data:
        raise ValueError("candle_data cannot be empty")

    if not timeframes:
        raise ValueError("At least one timeframe required")

    # Implementation
    pass
```

### Error Handling

Use specific exceptions and always handle errors appropriately.

**Bad:**
```python
def load_config():
    try:
        with open('config.json') as f:
            return json.load(f)
    except:
        return {}
```

**Good:**
```python
from typing import Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

def load_config(config_path: Path) -> Dict[str, Any]:
    """
    Load configuration from JSON file.

    Args:
        config_path: Path to configuration file

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        raise FileNotFoundError(f"Config not found: {config_path}")

    try:
        with config_path.open('r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        raise

    logger.info(f"Loaded configuration from {config_path}")
    return config
```

### Logging

Use proper logging instead of print statements.

**Bad:**
```python
def start_trading():
    print("Starting trading bot...")
    # code
    print("Trading started successfully!")
```

**Good:**
```python
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def start_trading(config: Dict[str, Any]) -> bool:
    """
    Start the trading bot with given configuration.

    Args:
        config: Trading configuration dictionary

    Returns:
        True if started successfully, False otherwise
    """
    logger.info("Initializing trading bot")

    try:
        # Validate configuration
        required_keys = ['api_key', 'coins', 'dca_levels']
        missing = [k for k in required_keys if k not in config]
        if missing:
            logger.error(f"Missing required config keys: {missing}")
            return False

        # Start trading
        logger.info("Starting trading engine")
        # Implementation

        logger.info("Trading bot started successfully")
        return True

    except Exception as e:
        logger.exception(f"Failed to start trading bot: {e}")
        return False
```

### Pathlib Over os.path

Use `pathlib.Path` instead of `os.path` for file operations.

**Bad:**
```python
import os

config_file = os.path.join(os.path.dirname(__file__), 'config', 'settings.json')
if os.path.exists(config_file):
    with open(config_file, 'r') as f:
        data = f.read()
```

**Good:**
```python
from pathlib import Path

config_file = Path(__file__).parent / 'config' / 'settings.json'
if config_file.exists():
    data = config_file.read_text(encoding='utf-8')
```

### Context Managers

Always use context managers for resource management.

**Bad:**
```python
def save_data(filename, data):
    f = open(filename, 'w')
    json.dump(data, f)
    f.close()
```

**Good:**
```python
from pathlib import Path
from typing import Dict, Any
import json

def save_data(file_path: Path, data: Dict[str, Any]) -> None:
    """
    Save data to JSON file.

    Args:
        file_path: Path to output file
        data: Data to save

    Raises:
        IOError: If file cannot be written
    """
    try:
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Failed to save data to {file_path}: {e}")
        raise
```

## Architecture Patterns

### Dependency Injection

Avoid hard-coded dependencies, use dependency injection.

**Bad:**
```python
class Trader:
    def __init__(self):
        self.api = RobinhoodAPI()  # Hard-coded dependency

    def place_order(self, symbol, amount):
        return self.api.buy(symbol, amount)
```

**Good:**
```python
from typing import Protocol

class TradingAPI(Protocol):
    """Protocol defining trading API interface."""

    def buy(self, symbol: str, amount: float) -> Dict[str, Any]:
        """Place a buy order."""
        ...

    def sell(self, symbol: str, amount: float) -> Dict[str, Any]:
        """Place a sell order."""
        ...

class Trader:
    """Trading engine with injected API dependency."""

    def __init__(self, api: TradingAPI) -> None:
        """
        Initialize trader with API client.

        Args:
            api: Trading API client implementing TradingAPI protocol
        """
        self._api = api
        self._logger = logging.getLogger(__name__)

    def place_order(self, symbol: str, amount: float) -> Optional[Dict[str, Any]]:
        """
        Place a buy order for specified amount.

        Args:
            symbol: Trading symbol (e.g., 'BTC')
            amount: Amount to buy

        Returns:
            Order details if successful, None otherwise
        """
        try:
            result = self._api.buy(symbol, amount)
            self._logger.info(f"Placed order: {symbol} x {amount}")
            return result
        except Exception as e:
            self._logger.error(f"Order failed: {e}")
            return None
```

### Configuration Management

Use dataclasses or Pydantic for configuration.

**Bad:**
```python
config = {
    'api_key': 'xxx',
    'coins': ['BTC', 'ETH'],
    'dca_percent': 0.05
}
# Access with config['api_key'], prone to typos and no validation
```

**Good:**
```python
from dataclasses import dataclass, field
from typing import List

@dataclass
class TradingConfig:
    """Trading bot configuration."""

    api_key: str
    api_secret: str
    coins: List[str] = field(default_factory=list)
    dca_percent: float = 0.05
    max_dca_levels: int = 5
    trailing_margin: float = 0.025

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.api_key or not self.api_secret:
            raise ValueError("API credentials required")

        if not 0 < self.dca_percent < 1:
            raise ValueError("dca_percent must be between 0 and 1")

        if self.max_dca_levels < 1:
            raise ValueError("max_dca_levels must be positive")

        if not self.coins:
            raise ValueError("At least one coin required")

    @classmethod
    def from_file(cls, config_path: Path) -> 'TradingConfig':
        """Load configuration from JSON file."""
        data = json.loads(config_path.read_text())
        return cls(**data)

# Usage
config = TradingConfig(
    api_key="xxx",
    api_secret="yyy",
    coins=['BTC', 'ETH']
)
```

### Subprocess Management for PyInstaller

Handle subprocess launching correctly for bundled applications.

**Bad:**
```python
import subprocess

def start_thinker():
    subprocess.Popen(['python', 'pt_thinker.py'])
```

**Good:**
```python
import sys
import subprocess
from pathlib import Path
from typing import Optional

class SubprocessManager:
    """Manages subprocess execution for bundled and unbundled environments."""

    @staticmethod
    def get_executable_path(script_name: str) -> Path:
        """
        Get correct path to executable/script for current environment.

        Args:
            script_name: Name of script (e.g., 'pt_thinker.py' or 'pt_thinker')

        Returns:
            Path to executable or script

        Raises:
            FileNotFoundError: If executable/script not found
        """
        if getattr(sys, 'frozen', False):
            # Running as bundled executable
            base_path = Path(sys._MEIPASS)
            # Look for compiled executable (no .py extension)
            exe_name = script_name.replace('.py', '')
            if sys.platform == 'win32':
                exe_name += '.exe'

            exe_path = base_path / exe_name
            if not exe_path.exists():
                raise FileNotFoundError(f"Executable not found: {exe_path}")
            return exe_path
        else:
            # Running as script
            script_path = Path(__file__).parent / script_name
            if not script_path.exists():
                raise FileNotFoundError(f"Script not found: {script_path}")
            return script_path

    @staticmethod
    def start_subprocess(script_name: str) -> Optional[subprocess.Popen]:
        """
        Start subprocess in correct environment.

        Args:
            script_name: Name of script to run

        Returns:
            Popen object if successful, None otherwise
        """
        logger = logging.getLogger(__name__)

        try:
            exe_path = SubprocessManager.get_executable_path(script_name)

            if getattr(sys, 'frozen', False):
                # Bundled: run executable directly
                cmd = [str(exe_path)]
            else:
                # Script: run with Python interpreter
                cmd = [sys.executable, str(exe_path)]

            logger.info(f"Starting subprocess: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            return process

        except Exception as e:
            logger.error(f"Failed to start {script_name}: {e}")
            return None
```

## Testing Standards

### Unit Tests Required

All new functions must have unit tests.

```python
# test_resource_path.py
import sys
import pytest
from pathlib import Path
from unittest.mock import patch
from utils import get_resource_path

class TestGetResourcePath:
    """Test suite for get_resource_path function."""

    def test_unbundled_environment(self, tmp_path):
        """Test resource path resolution in development environment."""
        # Create test resource
        test_file = tmp_path / "test_resource.txt"
        test_file.write_text("test")

        # Mock __file__ to point to tmp_path
        with patch('utils.__file__', str(tmp_path / 'utils.py')):
            result = get_resource_path("test_resource.txt")
            assert result.exists()
            assert result.name == "test_resource.txt"

    def test_bundled_environment(self, tmp_path):
        """Test resource path resolution in PyInstaller bundle."""
        # Create test resource
        test_file = tmp_path / "test_resource.txt"
        test_file.write_text("test")

        # Mock frozen environment
        with patch.object(sys, 'frozen', True, create=True):
            with patch.object(sys, '_MEIPASS', str(tmp_path), create=True):
                result = get_resource_path("test_resource.txt")
                assert result.exists()

    def test_missing_resource_raises_error(self):
        """Test that FileNotFoundError is raised for missing resources."""
        with pytest.raises(FileNotFoundError):
            get_resource_path("nonexistent_file.txt")
```

### Integration Tests

Test the interaction between components.

```python
# test_subprocess_integration.py
import pytest
from pathlib import Path
from subprocess_manager import SubprocessManager

class TestSubprocessIntegration:
    """Integration tests for subprocess management."""

    @pytest.mark.slow
    def test_start_thinker_subprocess(self):
        """Test starting pt_thinker subprocess."""
        manager = SubprocessManager()
        process = manager.start_subprocess('pt_thinker.py')

        assert process is not None
        assert process.poll() is None  # Process is running

        # Cleanup
        process.terminate()
        process.wait(timeout=5)
```

## PyInstaller-Specific Code Quality

### Spec File Organization

```python
# pt_hub.spec
# -*- mode: python ; coding: utf-8 -*-

"""
PyInstaller spec file for PowerTrader AI Hub.

This file defines the build configuration for creating a standalone
executable of the PowerTrader AI main GUI application.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path

# Configuration
APP_NAME = 'PowerTrader_AI'
ENTRY_POINT = 'pt_hub.py'
ICON_FILE = 'assets/icon.ico'  # Windows
ICON_FILE_MAC = 'assets/icon.icns'  # macOS

# Collect data files
datas = []
datas += collect_data_files('matplotlib')
datas += [('assets', 'assets')]

# Hidden imports
hiddenimports = []
hiddenimports += collect_submodules('matplotlib.backends')
hiddenimports += collect_submodules('cryptography')
hiddenimports += [
    'PIL._imagingtk',
    'PIL._tkinter_finder',
    'kucoin.base_request',
]

# Analysis
a = Analysis(
    [ENTRY_POINT],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'unittest',
        '_pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# PYZ
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=None
)

# EXE
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_FILE
)

# macOS Bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name=f'{APP_NAME}.app',
        icon=ICON_FILE_MAC,
        bundle_identifier='com.powertrader.ai',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
        },
    )
```

## Code Review Checklist

Before committing, verify:

- [ ] All functions have type hints
- [ ] All public functions have docstrings
- [ ] No print statements (use logging)
- [ ] No bare except clauses
- [ ] No hard-coded paths (use Path)
- [ ] Proper error handling with specific exceptions
- [ ] No commented-out code
- [ ] No TODO comments without issue numbers
- [ ] Tests added for new functionality
- [ ] Tests pass locally
- [ ] PEP 8 compliant (run `black` and `flake8`)
- [ ] Type checking passes (run `mypy`)
- [ ] No security vulnerabilities (no hardcoded secrets)

## Tools to Use

### Code Formatting
```bash
pip install black isort
black --line-length 100 .
isort .
```

### Linting
```bash
pip install flake8 pylint
flake8 --max-line-length=100 .
pylint pt_hub.py
```

### Type Checking
```bash
pip install mypy
mypy --strict pt_hub.py
```

### Security
```bash
pip install bandit
bandit -r .
```

## Summary

Senior-level code is characterized by:
1. Comprehensive type hints and documentation
2. Proper error handling and logging
3. Testable architecture with dependency injection
4. Modern Python idioms (pathlib, dataclasses, context managers)
5. Security awareness
6. Performance consideration
7. Maintainability focus

Write code as if the next person to maintain it is a senior developer who knows where you live.
