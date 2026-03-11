"""
metadata.py - Extract metadata from images (PNG ComfyUI chunks, EXIF)
"""
import json
import struct
import zlib
from pathlib import Path
from functools import lru_cache
from utils.settings import get_setting

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
        "Filename": p.name,
        "Path": str(p),
        "Size": _fmt_size(stat.st_size),
        "Modified": _fmt_mtime(stat.st_mtime),
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
                result["basic"]["Dimensions"] = f"{w} × {h} px"
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
    """Build basic info summary only (no ComfyUI metadata / EXIF)."""
    if not result["basic"]:
        return "無基本資訊"
    lines = ["**基本資訊**", ""]
    for k, v in result["basic"].items():
        lines.append(f"{k}: {v}  ")
    return "\n".join(lines)


def _fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_mtime(mtime: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def get_comment_text(meta: dict) -> str:
    """Extract prompt/comment text from PNG metadata comment field.
    Reads meta['comfyui']['comment'] or similar keys.
    Falls back to exif ImageDescription / UserComment."""
    comfyui = meta.get("comfyui", {})
    # Try common comment-related keys (case insensitive search)
    for key in comfyui:
        if key.lower() in ("comment", "description", "parameters", "caption"):
            val = comfyui[key]
            if isinstance(val, str) and val.strip():
                return val.strip()
    # EXIF fallback
    exif = meta.get("exif", {})
    for key in ("UserComment", "ImageDescription"):
        if exif.get(key, "").strip():
            return exif[key].strip()
    return ""


def _extract_text_fields(inputs: dict) -> str:
    """Extract text from inputs dict: text_0, text_1, ... then text/value fallback."""
    # Collect numbered fields: text_0, text_1, ...
    numbered = {}
    for k, v in inputs.items():
        if isinstance(v, str) and k.startswith("text_") and k[5:].isdigit():
            numbered[int(k[5:])] = v
    if numbered:
        return "\n".join(numbered[i] for i in sorted(numbered) if numbered[i].strip())
    # Fallback: text / value field
    for fld in ("text", "value"):
        v = inputs.get(fld, "")
        if isinstance(v, str) and len(v.strip()) >= 10:
            return v.strip()
    return ""


def get_prompt_summary(meta: dict) -> str:
    """Extract text prompts from ComfyUI prompt JSON for display.

    Priority:
      1. Nodes with _meta.title == "genPrompts" → inputs.text_0, text_1, ...
      2. CLIPTextEncode / PrimitiveStringMultiline / etc.
      3. Fallback: any node with long text in inputs
    """
    prompt_data = meta.get("comfyui", {}).get("prompt", {})
    if not prompt_data:
        return ""

    GEN_TITLE = get_setting("prompt_keyword", "genPrompts")
    TEXT_FIELD_CLASSES = {
        "CLIPTextEncode", "CLIPTextEncodeSDXL",
        "CLIPTextEncodeFlux", "CLIPTextEncodeHunyuan",
        "ShowText", "ShowText|pysssss",
        "StringConstant", "StringConstant|pysssss",
        "Note", "Text", "TextBox", "FluxGuidance",
    }
    VALUE_FIELD_CLASSES = {
        "PrimitiveStringMultiline", "StringMultiline",
        "Primitive", "CM_StringUnaryOperation",
        "String Literal", "StringLiteral",
    }
    MIN_LEN = 10

    gen_parts, other_parts = [], []

    for node_id, node in prompt_data.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        cls = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", cls)

        if title == GEN_TITLE:
            text = _extract_text_fields(inputs)
            if text and len(text.strip()) >= MIN_LEN:
                gen_parts.append(f"**[{node_id}] {title}**\n\n{text.strip()}")
            continue

        # Other known classes
        if cls in TEXT_FIELD_CLASSES:
            text = inputs.get("text", "")
        elif cls in VALUE_FIELD_CLASSES:
            text = inputs.get("value", "")
        else:
            text = ""
            for fld in ("text", "value", "prompt"):
                v = inputs.get(fld, "")
                if isinstance(v, str) and len(v) >= MIN_LEN:
                    text = v
                    break

        if text and isinstance(text, str) and len(text.strip()) >= MIN_LEN:
            other_parts.append(f"**[{node_id}] {title}**\n\n{text.strip()}")

    combined = gen_parts if gen_parts else other_parts
    return "\n\n---\n\n".join(combined) if combined else ""


@lru_cache(maxsize=512)
def get_prompt_text_quick(file_path: str) -> str:
    """Fast extraction of 'comment' text from PNG for overlay display.
    Reads the first tEXt/iTXt chunk with key matching comment/description/parameters."""
    p = Path(file_path)
    if not p.exists() or p.suffix.lower() != ".png":
        return ""
    try:
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
                    key = data[:sep].decode("latin-1").lower()
                    if key not in ("comment", "description", "parameters", "caption"):
                        continue
                    if chunk_type == "tEXt":
                        raw = data[sep + 1:].decode("latin-1", errors="replace")
                    else:
                        rest = data[sep + 3:]
                        sep2 = rest.index(b"\x00")
                        rest = rest[sep2 + 1:]
                        sep3 = rest.index(b"\x00")
                        raw = rest[sep3 + 1:].decode("utf-8", errors="replace")
                    if raw.strip():
                        return raw.strip()
                except Exception:
                    continue
    except Exception:
        pass
    return ""
