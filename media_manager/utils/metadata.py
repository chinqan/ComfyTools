"""
metadata.py - Extract metadata from images (PNG ComfyUI chunks, EXIF)
"""
import json
import struct
import zlib
from pathlib import Path
from functools import lru_cache

try:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import piexif
    HAS_PIEXIF = True
except ImportError:
    HAS_PIEXIF = False


def extract_metadata(file_path: str) -> dict:
    """
    Extract metadata from an image file.

    Returns a dict with:
        - basic: {filename, path, size, mtime, dimensions}
        - comfyui: {workflow, prompt} (if PNG with ComfyUI chunks)
        - exif: {ImageDescription, UserComment, ...} (if JPEG)
        - raw_text: human-readable summary string
    """
    p = Path(file_path)
    result = {
        "basic": {},
        "comfyui": {},
        "exif": {},
        "raw_text": "",
    }

    if not p.exists():
        return result

    stat = p.stat()
    result["basic"] = {
        "檔名 Filename": p.name,
        "路徑 Path": str(p),
        "大小 Size": _fmt_size(stat.st_size),
        "修改日期 Modified": _fmt_mtime(stat.st_mtime),
    }

    ext = p.suffix.lower()

    if ext == ".png":
        _extract_png(file_path, result)
    elif ext in {".jpg", ".jpeg", ".webp"}:
        _extract_exif(file_path, result)

    # Get image dimensions via PIL
    if HAS_PIL and ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        try:
            with Image.open(file_path) as img:
                w, h = img.size
                result["basic"]["尺寸 Dimensions"] = f"{w} × {h} px"
        except Exception:
            pass

    # Build human-readable summary
    result["raw_text"] = _build_summary(result)
    return result


def _extract_png(file_path: str, result: dict):
    """Parse PNG tEXt / iTXt chunks manually for ComfyUI metadata."""
    try:
        with open(file_path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return
            while True:
                length_bytes = f.read(4)
                if len(length_bytes) < 4:
                    break
                length = struct.unpack(">I", length_bytes)[0]
                chunk_type = f.read(4).decode("latin-1")
                data = f.read(length)
                f.read(4)  # CRC

                if chunk_type == "tEXt":
                    _parse_text_chunk(data, result)
                elif chunk_type == "iTXt":
                    _parse_itxt_chunk(data, result)
                elif chunk_type == "IEND":
                    break
    except Exception as e:
        result["comfyui"]["error"] = str(e)


def _parse_text_chunk(data: bytes, result: dict):
    """Parse tEXt chunk: key\x00value"""
    try:
        sep = data.index(b"\x00")
        key = data[:sep].decode("latin-1")
        value = data[sep + 1:].decode("latin-1", errors="replace")
        _store_chunk(key, value, result)
    except Exception:
        pass


def _parse_itxt_chunk(data: bytes, result: dict):
    """Parse iTXt chunk for UTF-8 encoded text."""
    try:
        sep = data.index(b"\x00")
        key = data[:sep].decode("utf-8", errors="replace")
        rest = data[sep + 1:]
        # compression_flag (1 byte), compression_method (1 byte)
        comp_flag = rest[0]
        rest = rest[2:]
        # language tag \x00
        sep2 = rest.index(b"\x00")
        rest = rest[sep2 + 1:]
        # translated keyword \x00
        sep3 = rest.index(b"\x00")
        text_bytes = rest[sep3 + 1:]
        if comp_flag == 1:
            text_bytes = zlib.decompress(text_bytes)
        value = text_bytes.decode("utf-8", errors="replace")
        _store_chunk(key, value, result)
    except Exception:
        pass


def _store_chunk(key: str, value: str, result: dict):
    """Store parsed chunk data."""
    key_lower = key.lower()
    try:
        parsed = json.loads(value)
        if key_lower == "workflow":
            result["comfyui"]["workflow"] = parsed
        elif key_lower == "prompt":
            result["comfyui"]["prompt"] = parsed
        else:
            result["comfyui"][key] = parsed
    except Exception:
        result["comfyui"][key] = value


def _extract_exif(file_path: str, result: dict):
    """Extract EXIF metadata (JPEG/WEBP)."""
    if not HAS_PIEXIF:
        return
    try:
        exif_dict = piexif.load(file_path)
        zeroth = exif_dict.get("0th", {})
        exif = exif_dict.get("Exif", {})

        desc = zeroth.get(piexif.ImageIFD.ImageDescription)
        if desc:
            val = desc.decode("utf-8", errors="replace") if isinstance(desc, bytes) else str(desc)
            result["exif"]["ImageDescription"] = val

        comment = exif.get(piexif.ExifIFD.UserComment)
        if comment:
            # UserComment starts with charset identifier (8 chars)
            if isinstance(comment, bytes) and len(comment) > 8:
                try:
                    val = comment[8:].decode("utf-8", errors="replace")
                    result["exif"]["UserComment"] = val
                except Exception:
                    pass

        artist = zeroth.get(piexif.ImageIFD.Artist)
        if artist:
            result["exif"]["Artist"] = artist.decode("utf-8", errors="replace") if isinstance(artist, bytes) else str(artist)

        software = zeroth.get(piexif.ImageIFD.Software)
        if software:
            result["exif"]["Software"] = software.decode("utf-8", errors="replace") if isinstance(software, bytes) else str(software)

    except Exception:
        pass


def _build_summary(result: dict) -> str:
    lines = []

    if result["basic"]:
        lines.append("### 📋 基本資訊 Basic Info")
        for k, v in result["basic"].items():
            lines.append(f"**{k}:** {v}")

    if result["comfyui"]:
        lines.append("\n### 🤖 ComfyUI 元資料 Metadata")
        for k, v in result["comfyui"].items():
            if k in ("workflow", "prompt"):
                # Summarize, don't dump entire JSON
                if isinstance(v, dict):
                    count = len(v)
                    lines.append(f"**{k}:** {count} nodes (點擊「原始 JSON」查看完整內容)")
                else:
                    lines.append(f"**{k}:** {str(v)[:200]}")
            else:
                lines.append(f"**{k}:** {str(v)[:500]}")

    if result["exif"]:
        lines.append("\n### 📷 EXIF 資訊")
        for k, v in result["exif"].items():
            lines.append(f"**{k}:** {v[:500]}")

    return "\n".join(lines) if lines else "（無元資料）"


def _fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_mtime(mtime: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def get_prompt_summary(meta: dict) -> str:
    """Extract text prompts from ComfyUI prompt JSON for display.

    Supports multiple node types:
      - CLIPTextEncode / CLIPTextEncodeSDXL  → inputs.text
      - PrimitiveStringMultiline / StringMultiline → inputs.value
      - ShowText / StringConstant / Note → inputs.text or inputs.value
      - Any node whose inputs contain a long string (>20 chars)
    """
    prompt_data = meta.get("comfyui", {}).get("prompt", {})
    if not prompt_data:
        return ""

    # node class types that carry meaningful text in inputs.text
    TEXT_FIELD_CLASSES = {
        "CLIPTextEncode", "CLIPTextEncodeSDXL",
        "CLIPTextEncodeFlux", "CLIPTextEncodeHunyuan",
        "ShowText", "ShowText|pysssss",
        "StringConstant", "StringConstant|pysssss",
        "Note", "Text", "TextBox",
        "FluxGuidance",
    }
    # node class types that carry text in inputs.value
    VALUE_FIELD_CLASSES = {
        "PrimitiveStringMultiline", "StringMultiline",
        "Primitive", "CM_StringUnaryOperation",
        "String Literal", "StringLiteral",
    }

    MIN_TEXT_LEN = 10  # only collect strings longer than this

    collected: list[tuple[str, str, str]] = []  # (node_id, class_type, text)

    for node_id, node in prompt_data.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        class_type = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", class_type)

        text = None
        if class_type in TEXT_FIELD_CLASSES:
            text = inputs.get("text", "")
        elif class_type in VALUE_FIELD_CLASSES:
            text = inputs.get("value", "")
        else:
            # Fallback: check both fields for any node with meaningful text
            for field in ("text", "value", "prompt"):
                v = inputs.get(field, "")
                if isinstance(v, str) and len(v) >= MIN_TEXT_LEN:
                    text = v
                    break

        if text and isinstance(text, str) and len(text.strip()) >= MIN_TEXT_LEN:
            # Skip if it looks like a node reference list (list type)
            collected.append((node_id, title or class_type, text.strip()))

    if not collected:
        return ""

    parts = []
    for node_id, label, text in collected:
        header = f"**[{node_id}] {label}**"
        parts.append(f"{header}\n\n{text}")

    return "\n\n---\n\n".join(parts)


@lru_cache(maxsize=512)
def get_prompt_text_quick(file_path: str) -> str:
    """Fast extraction of prompt text from PNG for use in gallery data-prompt attribute.
    Results are cached by file path string. Returns plain text (no markdown)."""
    p = Path(file_path)
    if not p.exists() or p.suffix.lower() != ".png":
        return ""
    try:
        # Quick read: only parse PNG tEXt/iTXt chunks for 'prompt'
        with open(file_path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return ""
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                length = struct.unpack(">I", hdr[:4])[0]
                chunk_type = hdr[4:].decode("latin-1")
                data = f.read(length)
                f.read(4)  # CRC
                if chunk_type == "IEND":
                    break
                if chunk_type not in ("tEXt", "iTXt"):
                    continue
                try:
                    sep = data.index(b"\x00")
                    key = data[:sep].decode("latin-1")
                    if key.lower() != "prompt":
                        continue
                    if chunk_type == "tEXt":
                        raw = data[sep + 1:].decode("latin-1", errors="replace")
                    else:
                        rest = data[sep + 3:]
                        sep2 = rest.index(b"\x00")
                        rest = rest[sep2 + 1:]
                        sep3 = rest.index(b"\x00")
                        raw = rest[sep3 + 1:].decode("utf-8", errors="replace")
                    prompt_data = json.loads(raw)
                    texts = []
                    VALUE_CLS = {"PrimitiveStringMultiline", "StringMultiline", "Primitive"}
                    for node in prompt_data.values():
                        if not isinstance(node, dict):
                            continue
                        inp = node.get("inputs", {})
                        cls = node.get("class_type", "")
                        title = node.get("_meta", {}).get("title", cls)
                        text = inp.get("value", "") if cls in VALUE_CLS else inp.get("text", "")
                        if not text:
                            for fld in ("text", "value"):
                                v = inp.get(fld, "")
                                if isinstance(v, str) and len(v) >= 10:
                                    text = v
                                    break
                        if text and isinstance(text, str) and len(text.strip()) >= 10:
                            texts.append(f"[{title}]\n{text.strip()}")
                    return "\n\n---\n\n".join(texts)
                except Exception:
                    continue
    except Exception:
        pass
    return ""
