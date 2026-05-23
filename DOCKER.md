# PowerTrader AI — Docker Installation Guide

Docker Hub: [swedishhh/powertrader](https://hub.docker.com/repository/docker/swedishhh/powertrader)

---

## Prerequisites

| Platform | Install |
|----------|---------|
| **Mac** | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) |
| **Linux** | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose plugin](https://docs.docker.com/compose/install/) |
| **Windows** | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) (WSL2 backend required) |

---

## Quick Install (Mac / Linux)

```bash
# 1. Create your data directory
mkdir -p ~/powertrader

# 2. Create empty config (UI will populate it on first run)
touch ~/powertrader/pt_config.json

# 3. Create API keys file from the template below
touch ~/powertrader/exchange_api_keys.json

# 4. Download the compose file
curl -o docker-compose.yml \
  https://raw.githubusercontent.com/swedishhh/PowerTrader_AI/main/docker-compose.yml

# 5. Create .env pointing at your data directory
echo "POWERTRADER_ROOT=$HOME/powertrader" > .env

# 6. Pull the image and start
docker compose pull
docker compose up -d

# 7. Open the UI
open http://localhost:8080      # Mac
xdg-open http://localhost:8080  # Linux
```

---

## Quick Install (Windows)

Open **PowerShell**:

```powershell
# 1. Create your data directory
New-Item -ItemType Directory -Force "$env:USERPROFILE\powertrader"

# 2. Create empty config and API keys files
New-Item -ItemType File "$env:USERPROFILE\powertrader\pt_config.json"
New-Item -ItemType File "$env:USERPROFILE\powertrader\exchange_api_keys.json"

# 3. Download the compose file (save to a working folder, e.g. C:\powertrader-app)
New-Item -ItemType Directory -Force C:\powertrader-app
cd C:\powertrader-app
Invoke-WebRequest `
  -Uri https://raw.githubusercontent.com/swedishhh/PowerTrader_AI/main/docker-compose.yml `
  -OutFile docker-compose.yml

# 4. Create .env — use forward slashes in the path
"POWERTRADER_ROOT=C:/Users/$env:USERNAME/powertrader" | Out-File -Encoding ascii .env

# 5. Pull and start
docker compose pull
docker compose up -d
```

Open **http://localhost:8080** in your browser.

---

## Exchange API Keys

Edit `exchange_api_keys.json` in your data directory. Only include the exchanges you use:

```json
{
  "kraken": {
    "api_key": "YOUR_KRAKEN_KEY",
    "api_secret": "YOUR_KRAKEN_SECRET"
  },
  "kucoin": {
    "api_key": "YOUR_KUCOIN_KEY",
    "api_secret": "YOUR_KUCOIN_SECRET"
  }
}
```

Restart the container after editing:

```bash
docker compose restart
```

If `exchange_api_keys.json` is empty or missing, PowerTrader starts in **demo mode** (no live exchange connection).

---

## Data Directory Layout

After first run, your data directory will look like this:

```
~/powertrader/
├── exchange_api_keys.json   ← your API credentials (never commit this)
├── pt_config.json           ← settings saved from the UI
└── state/
    ├── hub_data/            ← trade history, account history, thinker state
    ├── coins/               ← per-coin neural model outputs
    └── historic_data/       ← OHLCV data (ArcticDB)
```

---

## Starting and Stopping

**Command line:**
```bash
docker compose up -d      # start in background
docker compose down       # stop
docker compose restart    # restart after config changes
docker compose logs -f    # follow logs
```

**Docker Desktop app (Mac / Windows):**

After running `docker compose up -d` once from the terminal, the stack appears in Docker Desktop and can be managed entirely from the GUI from that point on.

1. Open **Docker Desktop**
2. Click **Containers** in the left sidebar
3. You will see a `powertrader-ai` group (or the folder name containing `docker-compose.yml`)
4. Expand it to see the `powertrader` container

From there you can:

| Action | How |
|--------|-----|
| Start / Stop | Click the ▶ / ■ button next to the container |
| View logs | Click the container name → **Logs** tab |
| Open the UI | Click the port link **8080:8080** — opens `http://localhost:8080` |
| Open a terminal inside the container | Click the container name → **Terminal** tab |
| Restart | Stop then Start, or use the ↺ button |

To **update** to a new image without the terminal:

1. Docker Desktop → **Images** tab
2. Find `swedishhh/powertrader` → click **Pull** to fetch the latest
3. Go back to **Containers**, stop the container, then start it again — it will use the new image

---

## Updating

```bash
docker compose pull       # fetch the latest image
docker compose up -d      # restart with new image (data directory unchanged)
```

---

## Changing the Data Directory

Edit `.env` and change `POWERTRADER_ROOT`, then restart:

```bash
# .env
POWERTRADER_ROOT=/new/path/to/data
```

```bash
docker compose down
docker compose up -d
```

---

## Troubleshooting

**Port 8080 already in use**  
Change the port in `docker-compose.yml`:
```yaml
ports:
  - "9090:8080"   # access on http://localhost:9090
```

**Permission denied on Linux**  
The container runs as root. If your data directory is owned by another user, make it world-writable or change ownership:
```bash
chmod -R 777 ~/powertrader
```

**UI shows DEMO after adding API keys**  
Make sure `exchange_api_keys.json` is valid JSON and restart the container.

---

## For Maintainers — Publishing to Docker Hub

Build and push a new image from the repo root:

```bash
# Build (clones latest code from GitHub)
docker build -t swedishhh/powertrader:latest .

# Tag with version (optional)
docker tag swedishhh/powertrader:latest swedishhh/powertrader:1.0.0

# Push
docker login
docker push swedishhh/powertrader:latest
docker push swedishhh/powertrader:1.0.0   # if tagged
```

Users then get the update with `docker compose pull`.
