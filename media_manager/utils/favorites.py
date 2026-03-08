"""
favorites.py - Persist favorite file paths to a JSON file
"""
import json
from pathlib import Path

FAVORITES_FILE = Path(__file__).parent.parent / "favorites.json"


def load_favorites() -> set:
    if FAVORITES_FILE.exists():
        try:
            with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data)
        except Exception:
            return set()
    return set()


def save_favorites(favorites: set):
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(favorites), f, ensure_ascii=False, indent=2)


def toggle_favorite(path: str) -> tuple[set, bool]:
    """Toggle a file path in favorites. Returns (updated_favorites, is_now_favorite)."""
    favs = load_favorites()
    if path in favs:
        favs.discard(path)
        is_fav = False
    else:
        favs.add(path)
        is_fav = True
    save_favorites(favs)
    return favs, is_fav


def is_favorite(path: str) -> bool:
    return path in load_favorites()
