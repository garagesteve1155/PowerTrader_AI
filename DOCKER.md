# PowerTrader AI — Docker Install Guide

---

## 1. Install Docker Desktop

- **Mac / Windows:** https://www.docker.com/products/docker-desktop/
- **Linux:** https://docs.docker.com/desktop/install/linux/

Open Docker Desktop and wait for it to finish starting.

---

## 2. Create Your Data Folder

Create a folder on your computer — this is where PowerTrader stores all its data.

| Platform | Suggested path |
|----------|---------------|
| Mac | `/Users/yourname/powertrader` |
| Windows | `C:\Users\yourname\powertrader` |
| Linux | `/home/yourname/powertrader` |

Inside that folder, create two files using a text editor:

### `exchange_api_keys.json`
Your exchange API credentials. Only include the exchanges you use:

```json
{
  "kraken": {
    "api_key": "YOUR_KEY",
    "api_secret": "YOUR_SECRET"
  }
}
```

> **Mac:** Open TextEdit → Format → Make Plain Text → save as `exchange_api_keys.json`  
> **Windows:** Open Notepad → Save As → set type to *All Files* → name it `exchange_api_keys.json`

### `pt_config.json`
Create this file containing just `{}`. The app writes your settings here when you use the web UI.

---

## 3. Download and Edit `docker-compose.yml`

`docker-compose.yml` is the single configuration file that tells Docker where your data folder is and which port to use. You only need to edit it once.

**Mac / Linux** — open Terminal:
```bash
curl -o docker-compose.yml https://raw.githubusercontent.com/swedishhh/PowerTrader_AI/main/docker-compose.yml
```

**Windows** — open PowerShell:
```powershell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/swedishhh/PowerTrader_AI/main/docker-compose.yml -OutFile docker-compose.yml
```

Open `docker-compose.yml` in a text editor and:

- Replace `/Users/yourname/powertrader` **(three places)** with the full path to your data folder
- Change `8080` on the left side of the port line if that port is already in use on your machine

---

## 4. Pull and Run

Open a terminal in the folder containing `docker-compose.yml` and run:

**Mac / Linux:**
```bash
docker compose pull && docker compose up -d
```

**Windows (PowerShell):**
```powershell
docker compose pull; docker compose up -d
```

Then open **http://localhost:8080** in your browser.

---

## Managing the Container

In Docker Desktop → **Containers** you can start, stop, view logs, and open the UI via the port link.

---

## Updating

Run the same one-liner from the folder containing `docker-compose.yml`:

**Mac / Linux:**
```bash
docker compose pull && docker compose up -d
```

**Windows (PowerShell):**
```powershell
docker compose pull; docker compose up -d
```

Your data folder is never touched by updates.

---

## Troubleshooting

**Port 8080 already in use** — change the left side of the port line in `docker-compose.yml` (e.g. `9090:8080`) and access on `http://localhost:9090`.

**Starts in demo mode** — check `exchange_api_keys.json` is valid JSON, then restart: `docker compose restart`.

**Container exits immediately** — Docker Desktop → Containers → click the container name → Logs tab.

---

## For Maintainers — Publishing a New Image

```bash
# From the repo root — CACHEBUST forces a fresh git clone every build
docker build --build-arg CACHEBUST=$(date +%s) -t swedishhh/powertrader:latest .
docker login
docker push swedishhh/powertrader:latest
```

To also publish a versioned tag (e.g. `2.0.0`), add `-t` for each tag and push both:

```bash
DESC="Fixes BTC trainer segfault — get_patterns_matrix incremental rewrite"
REV=$(git ls-remote https://github.com/swedishhh/PowerTrader_AI.git HEAD | cut -f1)
VERSION="4.0.1"
docker build \
--build-arg CACHEBUST=$(date +%s) \
--label "org.opencontainers.image.description=$DESC" \
--label "org.opencontainers.image.revision=$REV" \
-t swedishhh/powertrader:latest \
-t swedishhh/powertrader:$VERSION . \
&& docker push swedishhh/powertrader:latest \
&& docker push swedishhh/powertrader:$VERSION
```
