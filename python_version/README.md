# Python Version - README

The original Python implementation of the PowerTrader AI trading system with full feature support including GUI, real exchange integration, and advanced neural network training.

## Quick Start

### Install Dependencies
```bash
cd python_version
pip install -r requirements.txt
```

### Run
```bash
python pt_hub.py
```

## Prerequisites

- **Python** >= 3.8
- **pip** (Python package manager)
- **API credentials** for KuCoin and/or Robinhood
- **GUI environment** (X11, Wayland, or Windows desktop)

## Files Overview

| File | Purpose |
|------|---------|
| `pt_hub.py` | Main GUI dashboard (Tkinter) |
| `pt_thinker.py` | Neural signal generator (TensorFlow) |
| `pt_trader.py` | Live trade execution and P&L tracking |
| `pt_trainer.py` | Model training and optimization |
| `requirements.txt` | Python dependencies |

## Key Features

- **Real-Time GUI Dashboard**: Live price updates, P&L tracking, signal visualization
- **Live Exchange Integration**: Direct API connections to KuCoin and Robinhood
- **Advanced Neural Networks**: TensorFlow-based price prediction and signal generation
- **Atomic File Operations**: Secure writes for trading data
- **Permission Enforcement**: API key protection with file-level restrictions
- **Comprehensive Logging**: Full audit trail of all trades and signals

## Architecture

### Components

**pt_hub.py** (Main Process)
- Tkinter GUI for dashboard
- Real-time matplotlib charts
- Signal and trade monitoring
- Status display

**pt_thinker.py** (Neural Thread)
- Fetches historical price data
- Runs TensorFlow neural network
- Generates buy/sell signals
- Outputs signal files per coin

**pt_trader.py** (Trading Thread)
- Reads neural signals
- Executes orders on exchanges
- Tracks P&L and positions
- Appends to trade history

**pt_trainer.py** (Training Process)
- Collects training data
- Trains neural models
- Updates model weights
- Benchmarks performance

### Data Flow
```
Real Exchange APIs (KuCoin, Robinhood)
    ↓
pt_thinker (neural inference)
    ↓
Signal files (long_dca_signal.txt, short_dca_signal.txt)
    ↓
pt_trader (live trading)
    ↓
Trade history & P&L (JSONL files in hub_data/)
    ↓
pt_hub GUI (visualization)
```

## Configuration

### API Keys
Create credential files in project root:
```bash
# KuCoin credentials
echo "your_kucion_key" > r_key.txt
echo "your_kucion_secret" > r_secret.txt
chmod 600 r_secret.txt

# Robinhood credentials (optional)
echo '{"username": "...", "password": "..."}' > robinhood_creds.json
chmod 600 robinhood_creds.json
```

### Trading Parameters
Edit configuration in each script:
- **pt_thinker.py**: Neural model parameters, lookback period, signal thresholds
- **pt_trader.py**: Position sizing, risk limits, order types
- **pt_trainer.py**: Training epochs, batch size, learning rate

## Monitoring & Logging

### Real-Time Dashboard
Launch `pt_hub.py` to see:
- Current prices and positions
- Live P&L updates
- Buy/sell signals
- Account value chart

### Log Files
All activity logged to files in `hub_data/`:
- `trade_history.jsonl`: All executed trades
- `account_value_history.jsonl`: Portfolio value over time
- `trader_status.json`: Current positions and balance
- `pnl_ledger.json`: Per-trade profit/loss

### Command Line
Check signals manually:
```bash
cat BTC_folder/long_dca_signal.txt
cat BTC_folder/short_dca_signal.txt
```

## Security Best Practices

### API Key Protection
- ✅ Always use `chmod 600` on secret files
- ✅ Never commit credentials to version control
- ✅ Use `.gitignore` to exclude `*_key.txt` and `*_secret.txt`
- ✅ Rotate API keys periodically
- ✅ Use IP whitelisting on exchanges

### File Permissions
```bash
# Verify permissions before trading
ls -l r_secret.txt      # Should show: -rw------- 
ls -ld hub_data/        # Should show: d---------
ls -l hub_data/*        # Should show: -rw-------
```

### Network Security
- Use VPN when accessing exchanges
- Enable 2FA on all exchange accounts
- Use API keys with minimal required permissions (read-only for thinker, trade-only for trader)
- Monitor API access logs regularly

### Audit Trail
Review these files regularly:
```bash
tail -100 hub_data/trade_history.jsonl
jq '.timestamp, .symbol, .quantity, .price' hub_data/trade_history.jsonl
```

## Testing & Validation

### Dry Run (Paper Trading)
1. Set exchange API keys to paper-trading account
2. Run normally; no real trades will execute
3. Monitor signals and verify logic

### Unit Testing
```bash
# Test signal generation
python -c "from pt_thinker import calculate_signals; print(calculate_signals('BTC'))"

# Verify file I/O
python -c "from pt_trader import read_trader_status; print(read_trader_status())"
```

### Performance Profiling
```bash
python -m cProfile -s cumulative pt_thinker.py BTC
```

## Troubleshooting

### Module Import Errors
```bash
# Reinstall requirements
pip install --upgrade -r requirements.txt

# Check Python version
python --version  # Should be >= 3.8

# List installed packages
pip list
```

### Exchange Connection Issues
- Verify API keys: check exchange dashboard for key details
- Check network: `ping api.kucoin.com`
- Review logs: check `hub_data/` for connection errors
- Test credentials: `python -c "from pt_trader import test_exchange_connection"`

### GUI Not Displaying
- Ensure X11/Wayland: `echo $DISPLAY` (Linux)
- Check Tkinter: `python -m tkinter` (should open window)
- Try remote X11: `ssh -X user@host` (if SSH)

### Performance Issues
- **Slow signals**: Reduce neural model complexity or increase lookback period
- **High memory**: Check historical data size, limit price history retention
- **API rate limits**: Add delays between requests, use batch API endpoints
- **Disk space**: Archive old JSONL files to separate storage

## Environment Variables

None required, but optional:
```bash
# Reduce output verbosity
export LOG_LEVEL=WARNING

# Use alternative exchange (future feature)
export EXCHANGE=KRAKEN
```

## Comparison with C Version

| Feature | Python | C |
|---------|--------|---|
| **Full GUI** | ✅ | ❌ |
| **Live API** | ✅ | ❌ |
| **Neural Networks** | ✅ (TensorFlow) | ❌ |
| **Startup Speed** | ~1s | ~2ms |
| **Memory** | ~100MB | ~10MB |
| **Use Case** | **Production trading** | **Testing, minimal system** |

## Dependencies Explained

See `requirements.txt` for full list. Key packages:
- **tensorflow**: Deep learning for neural networks
- **numpy/pandas**: Numerical computing
- **matplotlib**: Charting for GUI
- **requests**: HTTP for exchange APIs
- **cryptography**: API signature generation

## License

See [LICENSE](../LICENSE) in parent directory.

## Related Documentation

- [SECURITY.md](SECURITY.md) - API key and data protection
- [../../README.md](../../README.md) - Project overview and comparison

## Support

For issues with specific exchanges:
- **KuCoin**: https://docs.kucoin.com
- **Robinhood**: https://robinhood.com/api-docs

For Python-specific issues:
- Check Python version compatibility
- Review virtualenv setup
- Test with minimal dependencies

---

**Warning**: This implementation trades with real money. Test thoroughly before enabling live trading.
