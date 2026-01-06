import os
from typing import Optional


def load_env(path: Optional[str] = None, override: bool = False) -> Optional[str]:
    """
    Load environment variables from a .env file.
    Returns the path that was loaded, or None if not found.
    """
    candidates = []
    if path:
        candidates.append(path)

    env_path = os.environ.get("POWERTRADER_ENV")
    if env_path:
        candidates.append(env_path)

    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    target = None
    for cand in candidates:
        if cand and os.path.isfile(cand):
            target = cand
            break

    if not target:
        return None

    try:
        with open(target, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if raw.startswith("export "):
                    raw = raw[7:].strip()
                if "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if not override and key in os.environ:
                    continue
                os.environ[key] = value
    except Exception:
        return None

    return target
