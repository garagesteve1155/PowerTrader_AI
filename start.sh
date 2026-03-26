#!/usr/bin/env bash
set -e

# start.sh - Optional VNC/Xvfb startup helper
# If ENABLE_VNC is set (non-empty), start Xvfb, fluxbox window manager, and x11vnc
# Then run the provided command (default: python pt_hub.py)

ENABLE_VNC=${ENABLE_VNC:-}
DISPLAY_NUM=${DISPLAY_NUM:-1}
XVFB_DISPLAY=":${DISPLAY_NUM}"

# Allow configurable virtual screen size via env vars (defaults keep previous behavior)
SCREEN_WIDTH=${SCREEN_WIDTH:-1280}
SCREEN_HEIGHT=${SCREEN_HEIGHT:-800}
SCREEN_DEPTH=${SCREEN_DEPTH:-24}

if [ -n "$ENABLE_VNC" ]; then
  echo "[start.sh] ENABLE_VNC detected — starting Xvfb on display ${XVFB_DISPLAY} (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})"

  # Clear stale locks from an unclean restart. If Xvfb is truly running, it will
  # recreate these; if not, they would block a clean startup.
  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true

  Xvfb ${XVFB_DISPLAY} -ac -screen 0 ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH} >/tmp/xvfb.log 2>&1 &
  export DISPLAY=${XVFB_DISPLAY}

  # Wait for the virtual display to be ready before launching anything that needs Tk/X.
  for _ in $(seq 1 50); do
    if xdpyinfo -display "${XVFB_DISPLAY}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done

  if ! xdpyinfo -display "${XVFB_DISPLAY}" >/dev/null 2>&1; then
    echo "[start.sh] ERROR: Xvfb did not become ready on ${XVFB_DISPLAY}" >&2
    cat /tmp/xvfb.log >&2 || true
    exit 1
  fi

  echo "[start.sh] Starting fluxbox window manager"
  fluxbox >/tmp/fluxbox.log 2>&1 &

  echo "[start.sh] Starting x11vnc on ${XVFB_DISPLAY} (port ${VNC_PORT:-5900})"
  x11vnc -display ${XVFB_DISPLAY} -forever -nopw -shared -rfbport ${VNC_PORT:-5900} >/tmp/x11vnc.log 2>&1 &
else
  echo "[start.sh] ENABLE_VNC not set — running command without virtual display"
fi

echo "[start.sh] Executing: $@"
exec "$@"
