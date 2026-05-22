#!/bin/bash
set -e

# Generate exchange_api_keys.json from environment variables.
# Only includes an exchange if at least one key is set.
python3 - <<'EOF'
import os, json

exchanges = ["kraken", "binance", "robinhood", "kucoin"]
keys = {}
for ex in exchanges:
    api_key    = os.environ.get(f"{ex.upper()}_API_KEY", "").strip()
    api_secret = os.environ.get(f"{ex.upper()}_API_SECRET", "").strip()
    if api_key or api_secret:
        keys[ex] = {"api_key": api_key, "api_secret": api_secret}

with open("/app/exchange_api_keys.json", "w") as f:
    json.dump(keys, f, indent=2)

if keys:
    print(f"[entrypoint] wrote exchange_api_keys.json for: {', '.join(keys)}")
else:
    print("[entrypoint] warning: no exchange API keys set — running in demo mode only")
EOF

exec python3 pt_web.py "$@"
