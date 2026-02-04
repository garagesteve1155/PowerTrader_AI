# PowerTrader AI - Architecture Analysis

## Component Overview

PowerTrader AI consists of four main Python components that work together as a multi-process system:

```
┌─────────────────────────────────────────────────────────────┐
│                        pt_hub.py                            │
│                    (Main GUI - tkinter)                     │
│  - Settings management                                      │
│  - Process orchestration                                    │
│  - Real-time monitoring                                     │
│  - Chart visualization (matplotlib)                         │
└────────────┬────────────────────────────────┬───────────────┘
             │ subprocess.Popen                │
             │ [sys.executable, pt_thinker.py] │
             ▼                                 ▼
    ┌────────────────┐              ┌────────────────┐
    │ pt_thinker.py  │              │  pt_trainer.py │
    │ (AI Processor) │              │  (Training)    │
    │                │              │                │
    │ - Price data   │              │ - Historical   │
    │ - Pattern      │              │   data fetch   │
    │   matching     │              │ - Model        │
    │ - Signal       │◄─────────────┤   training     │
    │   generation   │  Uses models │                │
    └────────┬───────┘              └────────────────┘
             │ Signals via file I/O
             ▼
    ┌────────────────┐
    │  pt_trader.py  │
    │ (Trade Engine) │
    │                │
    │ - Robinhood    │
    │   API calls    │
    │ - Order        │
    │   execution    │
    │ - Position     │
    │   management   │
    └────────────────┘
```

## File Architecture

### Core Components

1. **pt_hub.py** (Main GUI)
   - Entry point for the application
   - tkinter-based graphical interface
   - Launches and monitors pt_thinker and pt_trader
   - Real-time chart visualization
   - Configuration management

2. **pt_thinker.py** (AI Signal Processor)
   - Runs as separate process
   - Fetches live price data from exchanges (Kucoin)
   - Performs pattern matching against trained models
   - Generates LONG/SHORT signals
   - Writes signals to data files

3. **pt_trader.py** (Trading Engine)
   - Runs as separate process
   - Reads signals from pt_thinker
   - Executes trades via Robinhood API
   - Manages DCA (Dollar Cost Averaging) strategy
   - Handles trailing profit margins

4. **pt_trainer.py** (Model Training)
   - Launched on-demand for each coin
   - Fetches historical price data
   - Builds pattern database (AI "memory")
   - Saves trained models to coin folders

### Data Flow

```
User Settings (GUI)
       │
       ├──> Configuration Files (*.txt, *.json)
       │
       ├──> pt_trainer.py ──> Trained Models ──> Coin Folders (BTC/, ETH/, etc.)
       │                                                │
       │                                                │
       └──> pt_thinker.py ◄───────────────────────────┘
                 │                   (Loads models)
                 │
                 ├──> Price Data (Exchange APIs)
                 │
                 └──> Signal Files ──> pt_trader.py ──> Robinhood API
```

## Dependency Analysis

### pt_hub.py Dependencies

**GUI & Visualization:**
- `tkinter` - Main GUI framework
- `tkinter.ttk` - Themed widgets
- `matplotlib` - Chart generation
- `matplotlib.backends.backend_tkagg` - Tkinter integration

**System:**
- `subprocess` - Launch pt_thinker, pt_trader, pt_trainer
- `threading` - Background tasks
- `queue` - Thread communication
- `psutil` - Process monitoring

**Data Handling:**
- `json` - Configuration files
- `dataclasses` - Data structures

**Standard Library:**
- `os`, `sys`, `time`, `math`, `glob`, `shutil`, `bisect`

### pt_thinker.py Dependencies

**Networking & APIs:**
- `requests` - HTTP requests
- `kucoin.client.Market` - Kucoin exchange API

**Cryptography:**
- `nacl.signing.SigningKey` - Signing operations

**Data & Time:**
- `datetime`, `calendar`, `time`
- `json` - Data persistence

**System:**
- `os`, `sys`, `psutil`, `logging`, `traceback`

**Security:**
- `base64`, `hashlib`, `hmac`, `uuid`

### pt_trader.py Dependencies

**Cryptography:**
- `cryptography.hazmat.primitives.asymmetric.ed25519` - Signature creation
- `cryptography.hazmat.primitives.serialization` - Key serialization
- `nacl.signing.SigningKey` - Signing operations

**Networking:**
- `requests` - Robinhood API calls

**Terminal:**
- `colorama` - Colored console output

**Data:**
- `json`, `base64`, `uuid`

**Standard Library:**
- `datetime`, `time`, `math`, `os`, `traceback`

### pt_trainer.py Dependencies

(Assumed similar to pt_thinker.py based on coin folder copies)

## Multi-Process Architecture Issues

### Critical Issue: Subprocess Launching

**Current Implementation** (pt_hub.py, line ~3086):
```python
p.proc = subprocess.Popen(
    [sys.executable, "-u", p.path],
    cwd=self.project_dir,
    env=env,
)
```

**Problems when bundled:**
1. `sys.executable` points to PyInstaller bootloader, not Python
2. `p.path` references `.py` files that don't exist in bundle
3. Scripts are compiled into executables, not runnable as scripts

**Solution Required:**
- Detect if running bundled (`getattr(sys, 'frozen', False)`)
- When bundled, launch sibling executables (pt_thinker, pt_trader)
- When not bundled, use current Python script approach

## Data Files & Persistence

### Configuration Files
- `r_key.txt` - Robinhood API key (external, not bundled)
- `r_secret.txt` - Robinhood secret key (external, not bundled)
- Various JSON configuration files

### Coin Data Folders
```
BTC/
├── pt_trainer.py
├── *.json (trained models)
└── price_data/

ETH/
├── pt_trainer.py
├── *.json (trained models)
└── price_data/

[Other coins...]
```

**Bundling Strategy:**
- **DO NOT bundle** coin data folders (user-generated, large)
- **DO NOT bundle** API keys (security, user-specific)
- **DO bundle** default configuration templates (if any)

## Hidden Imports Required for PyInstaller

### Already Identified (in pt_hub.spec)
- `matplotlib.backends.backend_tkagg`
- `matplotlib.backends.backend_agg`
- `PIL`, `PIL.Image`, `PIL.ImageTk`, `PIL._imagingtk`, `PIL._tkinter_finder`
- `cryptography` submodules
- `kucoin`, `kucoin.base_request`
- `urllib3`, `requests`, `certifi`, `charset_normalizer`
- `psutil`, `queue`, `threading`

### Additional Required
- `nacl.signing` (PyNaCl)
- `colorama` (for pt_trader colored output)
- `cryptography.hazmat.primitives.asymmetric.ed25519`
- `cryptography.hazmat.primitives.serialization`
- `kucoin.client` (if not covered by kucoin.base_request)

## Windows vs macOS Differences

### Platform-Specific Code (pt_hub.py)

**File Browser:**
```python
if sys.platform == "win32":
    os.startfile(folder)  # Windows
elif sys.platform == "darwin":
    subprocess.Popen(["open", folder])  # macOS
else:
    subprocess.Popen(["xdg-open", folder])  # Linux
```

**Implications:**
- macOS builds need `open` command support
- Windows builds need proper file association handling
- Both need platform detection in bundled state

## Build Requirements Summary

### For pt_hub.spec
- [x] Tkinter + Tcl/Tk data files
- [x] Matplotlib backends and data
- [x] PIL/Pillow image support
- [x] Cryptography libraries
- [x] Kucoin API client
- [x] Networking (requests, urllib3, certifi)
- [x] System utilities (psutil)
- [ ] PyNaCl library

### For pt_thinker.spec
- [ ] Kucoin API client
- [ ] PyNaCl signing
- [ ] Requests + dependencies
- [ ] Logging infrastructure
- [ ] JSON handling

### For pt_trader.spec
- [ ] Cryptography (Ed25519)
- [ ] PyNaCl signing
- [ ] Requests for API calls
- [ ] Colorama for terminal output
- [ ] JSON handling

## Testing Checklist

### Unit Testing (Pre-Bundle)
- [ ] pt_hub GUI launches successfully
- [ ] pt_hub can spawn subprocesses
- [ ] Settings save/load correctly
- [ ] Charts render with matplotlib
- [ ] File I/O operations work

### Integration Testing (Bundled)
- [ ] pt_hub.app launches without errors
- [ ] Subprocess path resolution works
- [ ] pt_thinker and pt_trader executables launch
- [ ] Inter-process communication functions
- [ ] All configuration files accessible
- [ ] Robinhood API connection works (with valid keys)

### Platform Testing
- [ ] macOS Intel (x86_64)
- [ ] macOS Apple Silicon (arm64)
- [ ] Windows 10 (x64)
- [ ] Windows 11 (x64)

## Next Steps

1. **Implement subprocess path resolution helper**
   - Create utility function to detect bundled vs script mode
   - Return correct executable paths for bundled environment
   - Update pt_hub.py to use new helper

2. **Create pt_thinker.spec and pt_trader.spec**
   - Mirror pt_hub.spec structure
   - Add component-specific dependencies
   - Test building all three components

3. **Test multi-process architecture**
   - Build all three executables
   - Verify pt_hub can launch pt_thinker and pt_trader
   - Confirm communication via files works

4. **Create build automation scripts**
   - build_mac.sh - Build all components on macOS
   - build_windows.bat - Build all components on Windows
   - Clean build directory before each build

## Security Considerations

**DO NOT bundle sensitive data:**
- API keys (r_key.txt, r_secret.txt)
- User credentials
- Trading history
- Personal configuration

**Include in .gitignore:**
- `r_key.txt`, `r_secret.txt`
- `*.key`, `*.secret`
- Coin data folders (BTC/, ETH/, etc.)
- hub_data/

## Performance Considerations

**Bundle Size Optimization:**
- Exclude unused matplotlib backends (Qt, Gtk, Wx)
- Exclude testing frameworks (pytest, unittest)
- Exclude development tools (IPython, jupyter)
- Use UPX compression where applicable

**Runtime Performance:**
- Multi-process architecture is maintained (good for parallel execution)
- File I/O for inter-process communication (acceptable for this use case)
- No significant performance degradation expected from bundling

---

**Analysis Date**: 2026-02-05
**Status**: Complete
**Next Task**: Implement subprocess path resolution helper (Task 1.4)
