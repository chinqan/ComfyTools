"""
settings.py - Persist user preferences to settings.json
"""
import json
from pathlib import Path

_SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

_DEFAULTS: dict = {
    "prompt_keyword": "genPrompts",
}

_cache: dict | None = None


def load_settings() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if _SETTINGS_PATH.exists():
        try:
            _cache = {**_DEFAULTS, **json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))}
            return _cache
        except Exception:
            pass
    _cache = dict(_DEFAULTS)
    return _cache


def save_settings(data: dict) -> None:
    global _cache
    current = load_settings()
    current.update(data)
    _cache = current
    _SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def get_setting(key: str, default=None):
    return load_settings().get(key, default)
