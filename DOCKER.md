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

## 3. Edit `docker-compose.yml`

Download `docker-compose.yml` from the repo and open it in a text editor.

Replace `/Users/yourname/powertrader` (three places) with the actual path to your data folder.

---

## 4. Pull and Run

Open a terminal in the folder containing `docker-compose.yml` and run:

```bash
docker compose pull
docker compose up -d
```

Then open **http://localhost:8080** in your browser.

---

## Managing the Container

In Docker Desktop → **Containers** you can start, stop, view logs, and open the UI via the port link.

---

## Updating

```bash
docker compose pull
docker compose up -d
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
