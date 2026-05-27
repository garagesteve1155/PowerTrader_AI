#!/bin/bash
set -e

# --- pt_config.json ---
# If the host file didn't exist, Docker creates a directory at the mount point.
# Remove it so pt_env falls back to built-in defaults on first run.
# On subsequent runs the UI writes a real file to the host path.
if [ -d /app/pt_config.json ]; then
    if ! rm -rf /app/pt_config.json 2>/dev/null; then
        echo "[entrypoint] ERROR: pt_config.json is mounted as a directory."
        echo "[entrypoint] The file must exist on the host before running 'docker compose up'."
        echo "[entrypoint] Create it as an empty '{}' then run 'docker compose up -d' again."
        exit 1
    fi
fi

# --- exchange_api_keys.json ---
# If the host file didn't exist, Docker creates a directory at the mount point.
# Also allow env-var fallback for deployments that don't bind-mount the file.
if [ -d /app/exchange_api_keys.json ]; then
    if ! rm -rf /app/exchange_api_keys.json 2>/dev/null; then
        echo "[entrypoint] ERROR: exchange_api_keys.json is mounted as a directory."
        echo "[entrypoint] The file must exist on the host before running 'docker compose up'."
        echo "[entrypoint] Create it (even as an empty '{}') then run 'docker compose up -d' again."
        exit 1
    fi
fi

if [ ! -s /app/exchange_api_keys.json ]; then
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
    print(f"[entrypoint] generated exchange_api_keys.json for: {', '.join(keys)}")
else:
    print("[entrypoint] no API keys found — starting in demo mode")
EOF
fi

exec python3 pt_web.py "$@"
