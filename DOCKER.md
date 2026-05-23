# PowerTrader AI — Docker Installation Guide

No terminal required. Everything is done through the Docker Desktop app.

---

## Step 1 — Install Docker Desktop

Download and install Docker Desktop for your platform:

- **Mac:** https://docs.docker.com/desktop/install/mac-install/
- **Windows:** https://docs.docker.com/desktop/install/windows-install/
- **Linux:** https://docs.docker.com/desktop/install/linux/

Open Docker Desktop and wait for it to finish starting (the whale icon in the menu bar / taskbar turns solid when ready).

---

## Step 2 — Pull the PowerTrader Image

1. In Docker Desktop, click **Images** in the left sidebar
2. Click **Search images to run** (the search bar at the top)
3. Type `swedishhh/powertrader` and press Enter
4. Click **Pull** next to `swedishhh/powertrader`
5. Wait for the download to complete — the image will appear in your Images list

---

## Step 3 — Create Your Data Folder

Create a folder on your computer where PowerTrader will store all its data (trading history, settings, market data). This folder persists across app updates.

**Mac:** Open Finder → Go to your home folder → New Folder → name it `powertrader`

**Windows:** Open File Explorer → navigate to `Documents` → Right-click → New Folder → name it `powertrader`

**Linux:** Create `/home/yourname/powertrader`

Inside that folder, create two files using a text editor (TextEdit on Mac, Notepad on Windows):

### `exchange_api_keys.json`

This file holds your exchange API credentials. Create it with the following content, filling in your own keys. Only include the exchanges you use — delete the others:

```json
{
  "kraken": {
    "api_key": "YOUR_KRAKEN_API_KEY",
    "api_secret": "YOUR_KRAKEN_API_SECRET"
  },
  "kucoin": {
    "api_key": "YOUR_KUCOIN_API_KEY",
    "api_secret": "YOUR_KUCOIN_API_SECRET"
  },
  "binance": {
    "api_key": "YOUR_BINANCE_API_KEY",
    "api_secret": "YOUR_BINANCE_API_SECRET"
  }
}
```

> **Mac tip:** In TextEdit, go to **Format → Make Plain Text** before typing, then save with the exact filename `exchange_api_keys.json`. When saving, make sure the file type is not `.txt`.

> **Windows tip:** In Notepad, choose **Save As**, set **Save as type** to **All Files**, and name it `exchange_api_keys.json`.

### `pt_config.json`

Create a second file named `pt_config.json` containing just:

```json
{}
```

This is where PowerTrader saves your settings. The app writes to it when you configure things through the web UI.

---

## Step 4 — Run the Container

1. In Docker Desktop, click **Images** in the left sidebar
2. Find `swedishhh/powertrader` in the list
3. Click the **▶ Run** button on the right

A dialog box opens. Click **Optional settings** to expand it, then fill in:

### Ports

Enter `8080` in the **Host port** field (or any free port on your machine, e.g. `9090`). The container port is fixed by the image and does not need to be set.

### Volumes

Click **+** to add each of the following three volume mounts. For each one, fill in the **Host path** (the path on your computer) and the **Container path** (fixed — always the value shown):

| Host path | Container path |
|-----------|---------------|
| `/Users/yourname/powertrader/state` | `/app/state` |
| `/Users/yourname/powertrader/exchange_api_keys.json` | `/app/exchange_api_keys.json` |
| `/Users/yourname/powertrader/pt_config.json` | `/app/pt_config.json` |

**Paths by platform:**

*Mac — replace `yourname` with your Mac username:*
```
/Users/yourname/powertrader/state
/Users/yourname/powertrader/exchange_api_keys.json
/Users/yourname/powertrader/pt_config.json
```

*Windows — replace `yourname` with your Windows username:*
```
C:\Users\yourname\powertrader\state
C:\Users\yourname\powertrader\exchange_api_keys.json
C:\Users\yourname\powertrader\pt_config.json
```

*Linux:*
```
/home/yourname/powertrader/state
/home/yourname/powertrader/exchange_api_keys.json
/home/yourname/powertrader/pt_config.json
```

> **Tip:** Some versions of Docker Desktop have a **Browse** button next to the host path field. Use it to navigate to the folder/file instead of typing the path.

### Container name (optional)

Give it a memorable name like `powertrader` so it is easy to find later.

4. Click **Run**

---

## Step 5 — Open the App

Once the container is running:

- In Docker Desktop → **Containers**, find `powertrader`
- Click the **8080:8080** port link — it opens `http://localhost:8080` in your browser
- Or just open your browser and go to **http://localhost:8080**

---

## Managing the Container

Everything is done from **Docker Desktop → Containers**:

| Action | How |
|--------|-----|
| **Start** | Click ▶ |
| **Stop** | Click ■ |
| **Restart** | Stop then Start |
| **View logs** | Click the container name → **Logs** tab |
| **Open terminal inside** | Click the container name → **Terminal** tab |

---

## Updating to a New Version

1. Docker Desktop → **Images** → find `swedishhh/powertrader` → click **Pull** to fetch the latest
2. Go to **Containers** → Stop the `powertrader` container
3. Delete the stopped container (click the bin icon) — *your data folder is untouched*
4. Go back to **Images** → click **▶ Run** on `swedishhh/powertrader` → fill in the same port and volume settings as before → **Run**

Your trading history, settings, and market data are all in your data folder and are not affected by updates.

---

## Troubleshooting

**The container starts but the UI won't load**
Make sure port 8080 isn't used by another app. Choose a different host port (e.g. `9090`) and access the UI at `http://localhost:9090`.

**The container shows "demo mode" even though I added API keys**
Check that `exchange_api_keys.json` is valid JSON (no typos, all quotes matched). Restart the container after fixing it.

**I see errors about missing files**
Make sure the host paths for all three volumes exist before starting the container. The `state` path is a folder — if it doesn't exist yet, create it. The two `.json` files must exist as files (not folders) before you run.

**Docker Desktop shows the container as "Exited"**
Click the container name and check the Logs tab for the error message.

---

## Data Folder Reference

After first run your data folder will contain:

```
powertrader/
├── exchange_api_keys.json   ← your exchange API credentials
├── pt_config.json           ← settings saved by the web UI
└── state/
    ├── hub_data/            ← trade history, account value history
    ├── coins/               ← per-coin neural model data
    └── historic_data/       ← OHLCV market data (large — do not delete)
```

Back up the entire `powertrader/` folder to preserve your history and settings.

---

## For Maintainers — Publishing to Docker Hub

Run these commands from the repo root whenever you want to ship a new image.

```bash
# Build (clones latest code from GitHub at build time)
docker build -t swedishhh/powertrader:latest .

# Tag with a version number (optional but recommended)
docker tag swedishhh/powertrader:latest swedishhh/powertrader:1.0.0

# Push
docker login
docker push swedishhh/powertrader:latest
docker push swedishhh/powertrader:1.0.0   # if tagged
```

Users receive the update the next time they click **Pull** in Docker Desktop → Images.
