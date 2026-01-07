# C Version - README

A high-performance C implementation of the PowerTrader AI trading system, optimized for security and minimal latency.

## Quick Start

### Build
```bash
cd c_version
make -j4
```

### Run Individual Programs
```bash
# Neural network thinker (generates signals)
./pt_thinker BTC

# Trader (executes trades)
./pt_trader

# Trainer (trains neural network)
./pt_trainer BTC

# Hub Console (displays status)
./hub_console
```

## Prerequisites

- **GCC** compiler (or Clang)
- **POSIX system** (Linux, macOS)
- **API credentials**: `rh00d.sct` file in project root
  - Format: JSON file with `api_key` and `private_key` fields
  - Set permissions: `chmod 600 rh00d.sct`

## Files Overview

| File | Purpose |
|------|---------|
| `pt_thinker.c` | Simplified neural signal generator |
| `pt_trader.c` | Trade execution and PnL tracking |
| `pt_trainer.c` | Model training simulator |
| `hub_console.c` | Status dashboard (console-based) |
| `common.h` | Shared utilities and helpers |
| `Makefile` | Build configuration |

## Security Features

- ✅ **File permission enforcement**: API keys checked for `0600` mode
- ✅ **Atomic writes**: JSON files written safely via temp + rename
- ✅ **Restrictive directories**: `hub_data/` created with `0700` permissions
- ✅ **No shell injection**: Uses POSIX APIs, not `system()`
- ✅ **Buffer safety**: All string operations bounds-checked

See [SECURITY.md](SECURITY.md) for detailed hardening guide.

## Performance

- **Startup**: ~2ms per program
- **Memory**: ~5-10MB per process
- **File I/O**: Single-pass reads/appends
- **JSON**: Simple string-based parsing

Build with `-O3 -march=native` for additional 10-20% speed improvement.

See [PERFORMANCE.md](PERFORMANCE.md) for optimization guide.

## Architecture

### Data Flow
```
pt_thinker (generates signals per coin)
    ↓
    Creates: long_dca_signal.txt, short_dca_signal.txt
    ↓
pt_trader (reads signals, executes trades)
    ↓
    Updates: hub_data/trader_status.json, trade_history.jsonl
    ↓
pt_trainer (trains model for next cycle)
    ↓
    Updates: trainer_status.json
```

### File Structure
```
hub_data/                           # Trading data (0700 perms)
├── trader_status.json              # Current positions (0600)
├── pnl_ledger.json                 # P&L by trade (0600)
├── account_value_history.jsonl     # Historical balances (0600)
├── trade_history.jsonl             # All trades (0600)
└── runner_ready.json               # Hub readiness flag
<coin>_folder/
├── low_bound_prices.html           # Neural bounds
├── high_bound_prices.html
├── long_dca_signal.txt             # Buy signal
├── short_dca_signal.txt            # Sell signal
└── trainer_last_training_time.txt  # Training timestamp
```

## Build Variants

### Standard (Default)
```bash
make
gcc -Wall -Wextra -O2 *.c -o program
```

### Production (Optimized)
```bash
gcc -Wall -Wextra -O3 -march=native -flto *.c -o program
```

### Debug
```bash
gcc -Wall -Wextra -g -DDEBUG *.c -o program_debug
gdb ./program_debug
```

### Clean
```bash
make clean
```

## Testing

### Smoke Test
```bash
# Generate signals
./pt_thinker BTC

# Check output
cat BTC_folder/long_dca_signal.txt

# Trade
./pt_trader

# View status
./hub_console
```

### Security Validation
```bash
# Check hub_data permissions (should be d-------)
ls -ld hub_data

# Check JSON file perms (should be -------)
ls -l hub_data/*.json

# Verify API key perms
ls -l r_secret.txt  # Should be -rw-------
```

### Performance Profiling
```bash
# Measure runtime and memory
/usr/bin/time -v ./pt_trader

# Advanced profiling (requires perf-tools)
perf record ./pt_trader
perf report
```

## Troubleshooting

### Compilation Errors
- Ensure GCC >= 7.0: `gcc --version`
- Check POSIX headers: `ls /usr/include/sys/stat.h`
- Verify permissions: `chmod u+x Makefile`

### Runtime Errors

**"Cannot read r_secret.txt"**
- Create file: `echo "your_secret" > r_secret.txt`
- Set perms: `chmod 600 r_secret.txt`

**"Permission denied on hub_data"**
- Check directory: `ls -ld hub_data` (should be `0700`)
- Manual fix: `chmod 700 hub_data && chmod 600 hub_data/*`

**"Signal file not created"**
- Verify `<coin>_folder/` exists: `ls -d BTC_folder/`
- Check pt_thinker output: `./pt_thinker BTC`

## Limitations

- **Network**: Simplified/stubbed (no live exchange API)
- **GUI**: Console-based only (no Tkinter)
- **Training**: Dummy algorithm (generates random bounds)
- **Exchanges**: No real KuCoin/Robinhood integration

**Recommendation**: Use for testing and file-I/O validation only; integrate with live APIs as needed.

## Environment Variables

Currently none required. Set umask before running for additional security:
```bash
umask 0077
./pt_trader
```

## Dependencies

- **Standard C Library** (libc)
- **POSIX API** (stdio, stdlib, unistd, sys/stat)
- No external libraries required

## License

See [LICENSE](../LICENSE) in parent directory.

## Related Documentation

- [SECURITY.md](SECURITY.md) - API key handling and hardening
- [PERFORMANCE.md](PERFORMANCE.md) - Optimization and profiling guide
- [../../README.md](../../README.md) - Project overview
