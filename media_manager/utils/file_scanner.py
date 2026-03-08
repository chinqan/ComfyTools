"""
file_scanner.py - Scan a folder for images and videos
"""
import os
from pathlib import Path
from datetime import datetime

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi"}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS


def scan_folder(folder_path: str, filter_type: str = "all", search: str = "") -> list[dict]:
    """
    Scan a folder and return a list of media file dicts sorted by mtime (newest first).

    Args:
        folder_path: Absolute path to the folder to scan.
        filter_type: "all", "image", or "video"
        search: Optional filename search string (case-insensitive)

    Returns:
        List of dicts with keys: path, name, type, mtime, mtime_str, size, size_str
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return []

    results = []
    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in ALL_EXTS:
            continue

        # Detect type
        ftype = "image" if ext in IMAGE_EXTS else "video"

        # Apply type filter
        if filter_type == "image" and ftype != "image":
            continue
        if filter_type == "video" and ftype != "video":
            continue

        # Apply search filter
        if search and search.lower() not in f.name.lower():
            continue

        stat = f.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        results.append({
            "path": str(f.resolve()),
            "name": f.name,
            "type": ftype,
            "mtime": mtime,
            "mtime_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size": size,
            "size_str": _fmt_size(size),
        })

    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


def _fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
