"""
app.py - ComfyUI Media Manager (Gradio 6+)

Features:
  - Browse images & videos from ComfyUI output folder
  - Native folder picker dialog (osascript / AppleScript on macOS)
  - View metadata (PNG ComfyUI chunks + EXIF)
  - Favorites (persistent JSON)
  - Download
  - Drag-slider image comparison
  - Sidebar layout: controls left, gallery right
"""

import json
import subprocess
from pathlib import Path

import gradio as gr

from utils.file_scanner import scan_folder
from utils.favorites import load_favorites, toggle_favorite, is_favorite, save_favorites
from utils.metadata import extract_metadata, get_comment_text, get_prompt_text_quick
from utils.settings import load_settings, save_settings


# ──────────────────────────────────────────────
# Native folder picker via AppleScript (macOS, thread-safe)
# ──────────────────────────────────────────────

def pick_folder() -> str:
    """
    Open the macOS native folder chooser via osascript.
    Works safely from Gradio background threads (no AppKit main-thread requirement).
    Returns the chosen POSIX path, or empty string if cancelled.
    """
    script = (
        'tell application "Finder"\n'
        '    activate\n'
        '    set chosen to choose folder with prompt "選擇 ComfyUI Output 資料夾"\n'
        '    return POSIX path of chosen\n'
        'end tell'
    )
    try:
        import subprocess
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120
        )
        path = result.stdout.strip()
        # Remove trailing slash that AppleScript sometimes adds
        return path.rstrip("/") if path else ""
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


# ──────────────────────────────────────────────
# Gallery helpers
# ──────────────────────────────────────────────

def _get_resolution(path: str) -> str:
    """Return 'WxH' resolution string quickly (header only), or filename fallback."""
    from pathlib import Path as _P
    ext = _P(path).suffix.lower()
    if ext in {".mp4", ".webm", ".mov", ".avi", ".mkv"}:
        return _P(path).stem[:16]  # show short name for videos
    try:
        from PIL import Image as _Img
        with _Img.open(path) as im:
            w, h = im.size
        return f"{w}×{h}"
    except Exception:
        return _P(path).name[:20]


import html

def _build_html_gallery(files, selected_set, type_prefix):
    if not files:
        return "<div style='padding:40px;color:#64748b;text-align:center;'>沒有找到媒體檔案。</div>"
        
    items = []
    for f in files:
        p = f["path"]
        name = f["name"]
        res = _get_resolution(p)
        sel_class = "selected" if p in selected_set else ""
        favorited = is_favorite(p)
        fav_attr = ' data-favorited="1"' if favorited else ""
        fav_badge = '<div class="cb-fav-badge">❤️</div>' if favorited else ""
        
        # Check if it's a video
        if f.get("type", "image") == "video" or p.lower().endswith(tuple([".mp4", ".webm", ".mov", ".avi", ".mkv"])):
            media_html = f"<div class='cb-media-placeholder'>🎥<br><br>{name}</div>"
        else:
            media_html = f"<img src='/gradio_api/file={p}' class='cb-image' loading='lazy'>"
            
        safe_path = html.escape(str(p))
        safe_name = html.escape(str(name))
            
        # Prompt text for overlay (PNG only, cached)
        prompt_text = get_prompt_text_quick(p) if p.lower().endswith(".png") else ""
        safe_prompt = html.escape(prompt_text).replace("\n", "&#10;") if prompt_text else ""
        prompt_attr = f' data-prompt="{safe_prompt}"' if safe_prompt else ""

        item_html = f"""
        <div class="cb-item {sel_class}" data-path="{safe_path}" data-type="{type_prefix}"{fav_attr}{prompt_attr}>
            <div class="cb-img-wrap">
                {media_html}
                {fav_badge}
                <div class="cb-check">✓</div>
            </div>
            <div class="cb-caption">{safe_name} | {res}</div>
        </div>
        """
        items.append(item_html)
    return f"<div class='cb-wrap'>{''.join(items)}</div>"


# ──────────────────────────────────────────────
# Drag-slider comparison HTML
# ──────────────────────────────────────────────


def build_slider_html(path_a: str, path_b: str, label_a: str = "A", label_b: str = "B") -> str:
    if not path_a or not path_b:
        return "<p style='color:#64748b;text-align:center;padding:60px 20px;font-size:15px'>請先在「瀏覽」頁掃描，選好兩張圖片後按「比對！」</p>"

    import uuid
    uid = uuid.uuid4().hex[:8]

    def src(p):
        return f"/gradio_api/file={p}"

    return f"""
<div id="cmproot-{uid}" style="font-family:sans-serif">

  <!-- Zoom control bar -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;padding:6px 12px;
              background:#f1f5f9;border-radius:8px;font-size:13px;color:#334155">
    <span>🔍 縮放</span>
    <input id="zoom-{uid}" type="range" min="30" max="150" value="100" step="5"
           style="flex:1;cursor:pointer;accent-color:#6366f1">
    <span id="zlabel-{uid}" style="min-width:38px;text-align:right;font-weight:600;color:#6366f1">100%</span>
  </div>

  <!-- Comparison container (width controlled by zoom) -->
  <div id="zw-{uid}" style="width:100%;overflow:hidden">
  <div id="cmp-{uid}" style="position:relative;width:100%;user-select:none;touch-action:none;
       border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.25);background:#111">

    <!-- Base image A -->
    <img src="{src(path_a)}" style="display:block;width:100%;height:auto">

    <!-- Image B (clipped right half) -->
    <div id="cmpb-{uid}" style="position:absolute;inset:0;overflow:hidden;clip-path:inset(0 50% 0 0)">
      <img src="{src(path_b)}" style="display:block;width:100%;height:auto;pointer-events:none">
    </div>

    <!-- Labels -->
    <span style="position:absolute;top:10px;left:10px;z-index:3;padding:3px 12px;
                 border-radius:20px;font-size:12px;font-weight:700;color:#fff;
                 background:rgba(0,0,0,.6);pointer-events:none">{label_a}</span>
    <span style="position:absolute;top:10px;right:10px;z-index:3;padding:3px 12px;
                 border-radius:20px;font-size:12px;font-weight:700;color:#fff;
                 background:rgba(0,0,0,.6);pointer-events:none">{label_b}</span>

    <!-- Drag handle (wide invisible hit area, thin visible line) -->
    <div id="hdl-{uid}" style="position:absolute;top:0;left:50%;width:44px;height:100%;
         transform:translateX(-50%);z-index:4;cursor:ew-resize;touch-action:none;
         display:flex;align-items:center;justify-content:center">
      <div style="position:absolute;left:50%;top:0;width:3px;height:100%;
                  background:linear-gradient(180deg,#6366f1,#a78bfa);transform:translateX(-50%)"></div>
      <div style="width:40px;height:40px;border-radius:50%;
                  background:linear-gradient(135deg,#6366f1,#a78bfa);z-index:1;
                  display:flex;align-items:center;justify-content:center;
                  font-size:16px;color:#fff;box-shadow:0 2px 12px rgba(99,102,241,.65)">⇔</div>
    </div>
  </div>
  </div>
</div>

<script>
(function(){{
  var uid = '{uid}';
  function ready() {{
    var c   = document.getElementById('cmp-'    + uid);
    var hdl = document.getElementById('hdl-'    + uid);
    var b   = document.getElementById('cmpb-'   + uid);
    var zw  = document.getElementById('zw-'     + uid);
    var zr  = document.getElementById('zoom-'   + uid);
    var zl  = document.getElementById('zlabel-' + uid);
    if (!c || !hdl) {{ setTimeout(ready, 300); return; }}

    /* ── Zoom ── */
    zr.addEventListener('input', function() {{
      zw.style.width = zr.value + '%';
      zl.textContent = zr.value + '%';
    }});

    /* ── Drag with Pointer Events + setPointerCapture (works in Gradio) ── */
    function setPos(clientX) {{
      var r = c.getBoundingClientRect();
      var p = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      hdl.style.left = (p * 100) + '%';
      b.style.clipPath = 'inset(0 ' + ((1 - p) * 100) + '% 0 0)';
    }}
    hdl.addEventListener('pointerdown', function(e) {{
      hdl.setPointerCapture(e.pointerId);
      e.preventDefault();
    }});
    hdl.addEventListener('pointermove', function(e) {{
      if (e.buttons) setPos(e.clientX);
    }});
    /* pointerup / pointercancel are handled automatically by capture release */
  }}
  ready();
}})();
</script>
"""


# ──────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────

APP_CSS = """
    /* Light theme base */
    body, .gradio-container { background: #f8f9fa !important; }

    /* Title */
    h1 {
        color: #4f46e5 !important;
        font-size: 1.5rem !important; font-weight: 800 !important;
        margin: 0 !important; padding: 0 !important;
    }
    .subtitle { color: #64748b !important; font-size: 12px !important; margin: 0 !important; }

    /* Sidebar */
    .sidebar {
        background: #f1f5f9 !important;
        border-right: 1px solid #e2e8f0 !important;
        min-width: 260px !important;
        max-width: 300px !important;
        padding: 0 !important;
    }

    /* Meta panel - plain text, no extra border */
    .meta-panel p, .meta-panel strong {
        font-size: 12px !important;
        color: #1e293b !important;
        line-height: 1.6 !important;
    }

    /* Smaller buttons in sidebar */
    .sidebar-btn { font-size: 13px !important; padding: 6px 10px !important; }

    /* Gallery gets full height */
    .main-gallery { flex: 1 !important; }

    /* HTML Gallery Layout */
    .cb-wrap {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 12px;
        padding: 4px;
    }
    #main-gallery, #fav-gallery {
        height: auto;
        overflow-y: visible;
    }

    /* Custom item wrapper */
    .cb-item {
        position: relative;
        width: 100%;
        display: flex;
        flex-direction: column;
        border-radius: 8px;
        overflow: hidden;
        background: #e2e8f0;
        cursor: pointer;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        transition: transform 0.1s, box-shadow 0.1s;
    }
    .cb-item:hover {
        transform: scale(0.98);
    }
    .cb-item.selected {
        box-shadow: 0 0 0 4px #4f46e5 inset, 0 2px 8px rgba(79, 70, 229, 0.4);
        transform: scale(0.96);
    }
    /* Image wrapper keeps square aspect ratio */
    .cb-img-wrap {
        position: relative;
        width: 100%;
        aspect-ratio: 1;
        overflow: hidden;
        flex-shrink: 0;
    }

    /* The Checkmark */
    .cb-check {
        position: absolute;
        top: 8px;
        right: 8px;
        background: #4f46e5;
        color: white;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 14px;
        font-weight: bold;
        opacity: 0;
        transition: opacity 0.2s;
        pointer-events: none;
        z-index: 10;
    }

    .cb-item.selected .cb-check {
        opacity: 1;
    }

    /* Favorite badge — top-left corner */
    .cb-fav-badge {
        position: absolute;
        top: 6px;
        left: 6px;
        font-size: 18px;
        line-height: 1;
        pointer-events: none;
        z-index: 10;
    }

    .cb-image {
        width: 100%;
        height: 100%;
        object-fit: contain;
        background-color: #f8f9fa;
        display: block;
        pointer-events: none;
    }

    .cb-media-placeholder {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 13px;
        text-align: center;
        padding: 10px;
        color: #475569;
        word-break: break-all;
    }

    /* Caption below the image, not overlapping */
    .cb-caption {
        width: 100%;
        background: linear-gradient(#fff, #e8edf3);
        color: #334155;
        font-size: 9px;
        padding: 3px 6px;
        line-height: 1.4;
        text-align: center;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        pointer-events: none;
        flex-shrink: 0;
    }

    /* Status bar */
    .status-bar { color: #4f46e5 !important; font-size: 13px !important; font-weight: 600 !important; }

    /* Prevent horizontal scrollbar */
    body, html { overflow-x: hidden !important; }

    /* Compact top bar —  1~2 lines height */
    #top-bar { padding: 4px 0 !important; align-items: center !important; gap: 6px !important; }
    #top-bar h3 { margin: 0 !important; font-size: 1rem !important; }
    #top-bar input[type="text"] { height: 34px !important; min-height: 34px !important; padding: 4px 8px !important; }
    #top-bar button { height: 34px !important; min-height: 34px !important; padding: 0 10px !important; }
    #top-bar select, #top-bar .wrap { height: 34px !important; min-height: 34px !important; }
    #top-bar .block { margin: 0 !important; padding: 0 !important; }
    #top-bar .row { gap: 4px !important; align-items: center !important; flex-wrap: nowrap !important; overflow: hidden !important; }

    /* Gallery thumbnail caption — smaller */
    .thumbnail-item > div:last-child,
    .thumbnail-small > div:last-child {
        font-size: 10px !important;
        color: #475569 !important;
        padding: 2px 4px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }

    /* Force ImageSlider to scale images correctly to fit */
    #cmp-slider,
    #cmp-slider .image-container,
    #cmp-slider .wrap,
    #cmp-slider .preview {
        height: 100% !important;
        width: 100% !important;
        min-height: 820px !important;
    }
    #cmp-slider img {
        object-fit: contain !important;
        width: 100% !important;
        height: 100% !important;
    }

    footer { display: none !important; }
    
    .hidden-btn { display: none !important; }
    
    .selection-count {
        display: inline-flex !important;
        align-items: center;
        justify-content: center;
        background: #e0e7ff;
        color: #4338ca;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 14px;
        font-weight: 600;
        margin-left: 10px;
        height: 100%;
        min-width: 120px;
    }
    .selection-count p {
        margin: 0 !important;
    }
"""


# ──────────────────────────────────────────────
# Main app
# ──────────────────────────────────────────────

DEFAULT_FOLDERS = [
    "/Users/chinqan/Downloads/照片修改",
    "/Users/chinqan-mac/Downloads/照片修改",
    "/home/opo_admin/ComfyUI/output",
]
DEFAULT_FOLDER = DEFAULT_FOLDERS[0]


def build_app():
    import typing
    _state: typing.Dict[str, typing.Any] = {
        "files": [],
        "selected_path": "",
        "selected_batch": set(),
        "fav_selected_batch": set(),
    }

    # ── Business logic ──────────────────────────


    def refresh_gallery(folder, filter_type, search):
        files = scan_folder(folder, filter_type, search)
        _state["files"] = files
        _state["selected_path"] = ""
        _state["selected_batch"] = set()
        choices = [f["name"] for f in files if f["type"] == "image"]
        
        gallery_update = _build_html_gallery(files, _state["selected_batch"], "main")
        select_a_update = gr.update(choices=choices, value=None)
        select_b_update = gr.update(choices=choices, value=None)
        thumb_a_update = gr.update(value=None)
        thumb_b_update = gr.update(value=None)
        
        count_str = f"**已選取: {len(_state['selected_batch'])} 張**"
        
        return (gallery_update, select_a_update, select_b_update, thumb_a_update, thumb_b_update) + _detail_view("") + (count_str,)

    def _detail_view(path: str):
        if not path:
            return "", "", gr.update(value=None, visible=False), gr.update(value=None, visible=False), "#### 🔎 詳情"
        meta = extract_metadata(path)
        summary = meta["raw_text"]
        comment = get_comment_text(meta)
        ext = Path(path).suffix.lower()
        is_video = ext in {".mp4", ".webm", ".mov", ".avi"}
        return (
            summary,
            comment,
            gr.update(value=None if is_video else path, visible=not is_video),
            gr.update(value=path if is_video else None, visible=is_video),
            gr.update(value="#### 🔎 影片詳情" if is_video else "#### 🔎 圖片詳情")
        )

    def on_main_click(clicked_path):
        if clicked_path:
            if clicked_path in _state["selected_batch"]:
                _state["selected_batch"].discard(clicked_path)
            else:
                _state["selected_batch"].add(clicked_path)
            _state["selected_path"] = clicked_path
        
        detail_updates = _detail_view(_state["selected_path"])
        n = len(_state["selected_batch"])
        count_str = f"**已選取: {n} 張**"
        dl_label = gr.update(label=_dl_btn_label(n))
        
        # We don't output the gallery HTML anymore, just the detail updates, count and dl btn label
        return detail_updates + (count_str, dl_label)

    def on_fav_toggle():
        path = _state["selected_path"]
        if not path:
            return "❤️ 取消收藏", _fav_gallery()
        _, is_fav = toggle_favorite(path)
        return ("❤️ 取消收藏" if is_fav else "🤍 改為收藏"), _fav_gallery()

    def _get_all_favorites():
        favs = load_favorites()
        fav_files = []
        for p_str in sorted(favs):
            p = Path(p_str)
            if p.exists():
                fav_files.append({
                    "path": str(p),
                    "name": p.name,
                    "type": "video" if p.suffix.lower() in {".mp4", ".webm", ".mov", ".avi"} else "image"
                })
        return fav_files

    def _fav_gallery():
        fav_files = _get_all_favorites()
        return _build_html_gallery(fav_files, _state["fav_selected_batch"], "fav")

    def on_download():
        path = _state["selected_path"]
        return str(path) if path and Path(str(path)).exists() else None

    def on_fav_click(clicked_path):
        if clicked_path:
            if clicked_path in _state["fav_selected_batch"]:
                _state["fav_selected_batch"].discard(clicked_path)
            else:
                _state["fav_selected_batch"].add(clicked_path)
            _state["selected_path"] = clicked_path
            
        # detail_updates = (meta, prompt, img, video, title) — 5 values
        detail_updates = _detail_view(_state["selected_path"])
        # Insert fav_fav_btn label update at index 2 to match _fo order
        meta, prompt_val, img_up, vid_up, title_up = detail_updates
        fav_label = "❤️ 取消收藏" if is_favorite(_state["selected_path"]) else "🤍 改為收藏"
        n = len(_state["fav_selected_batch"])
        count_str = f"**已選取: {n} 張**"
        dl_label = gr.update(label=_dl_btn_label(n))
        return (meta, prompt_val, fav_label, img_up, vid_up, title_up, count_str, dl_label)

    def on_compare(name_a, name_b):
        if not name_a or not name_b:
            return None, "", ""
        by_name = {f["name"]: f["path"] for f in _state["files"]}
        pa, pb = by_name.get(name_a, ""), by_name.get(name_b, "")
        meta_a = extract_metadata(pa)["raw_text"] if pa else ""
        meta_b = extract_metadata(pb)["raw_text"] if pb else ""
        # Return tuple for gr.ImageSlider
        return (pa, pb) if pa and pb else None, meta_a, meta_b

    def _dl_btn_label(count, prefix="下載"):
        if count == 0:
            return f"⬇️ 批量{prefix}"
        return f"⬇️ 批量{prefix}({count})"

    def on_batch_download_main():
        paths = list(_state["selected_batch"])
        result = on_batch_download(paths)
        return gr.update(value=result, label=_dl_btn_label(len(paths)))
        
    def on_batch_download_fav():
        paths = list(_state["fav_selected_batch"])
        result = on_batch_download(paths)
        return gr.update(value=result, label=_dl_btn_label(len(paths)))

    def on_batch_fav_main():
        """批量將已選圖片加入收藏後重渲染 gallery"""
        to_fav = list(_state["selected_batch"])
        if to_fav:
            favs = load_favorites()
            modified = False
            for p in to_fav:
                if p not in favs:
                    favs.add(p)
                    modified = True
            if modified:
                save_favorites(favs)
        # 重新生成 gallery HTML，讓收藏標記立即顯示
        return _build_html_gallery(_state["files"], _state["selected_batch"], "main")

    def on_batch_download(selected_paths):
        if not selected_paths:
            return None
        from pathlib import Path
        # 單張直接下載，不打包 zip
        if len(selected_paths) == 1:
            p = Path(selected_paths[0])
            return str(p) if p.exists() else None
        import tempfile
        import zipfile
        temp_dir = tempfile.gettempdir()
        zip_path = Path(temp_dir) / "comfyui_batch_download.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for path in selected_paths:
                if Path(path).exists():
                    zipf.write(path, Path(path).name)
        return str(zip_path)

    def on_batch_delete_main(folder, filter_type, search):
        # 使用者取消確認對話框
        if folder == "CANCEL":
            return tuple([gr.update() for _ in _ro] + [gr.skip()])
        # 只刪除非收藏的檔案
        to_delete = [p for p in _state["selected_batch"] if not is_favorite(p)]
        if not to_delete:
            return tuple([gr.update() for _ in _ro] + [gr.skip()])

        # 1) Do deletion (skip favorited files)
        for fp in to_delete:
            pth = Path(fp)
            if pth.exists():
                pth.unlink()
            _state["selected_batch"].discard(fp)
            if _state["selected_path"] == fp:
                _state["selected_path"] = ""

        # 2) Refresh state
        files = scan_folder(folder, filter_type, search)
        _state["files"] = files
        new_html = _build_html_gallery(files, _state["selected_batch"], "main")
        count_str = f"**已選取: {len(_state['selected_batch'])} 張**"

        # _ro covers [gallery, select_a, select_b, thumb_a, thumb_b] + _do (5 layout comps)
        # we also need to append the count_str
        return (new_html, gr.update(), gr.update(), gr.update(), gr.update()) + _detail_view("") + (count_str,)

    def on_fav_batch_remove():
        favs = load_favorites()
        modified = False
        for p in _state["fav_selected_batch"]:
            if p in favs:
                favs.remove(p)
                modified = True
        if modified:
            save_favorites(favs)
            
        _state["fav_selected_batch"] = set()
        _state["selected_path"] = ""
        count_str = f"**已選取: {len(_state['fav_selected_batch'])} 張**"
        return _build_html_gallery(_get_all_favorites(), set(), "fav"), count_str

    def on_select_all_main():
        _state["selected_batch"] = {f["path"] for f in _state["files"]}
        n = len(_state["selected_batch"])
        return f"**已選取: {n} 張**", gr.update(label=_dl_btn_label(n))
        
    def on_deselect_all_main():
        _state["selected_batch"] = set()
        return f"**已選取: 0 張**", gr.update(label=_dl_btn_label(0))

    def on_select_all_fav():
        fav_files = _get_all_favorites()
        _state["fav_selected_batch"] = {f["path"] for f in fav_files}
        n = len(_state["fav_selected_batch"])
        return f"**已選取: {n} 張**", gr.update(label=_dl_btn_label(n))
        
    def on_deselect_all_fav():
        _state["fav_selected_batch"] = set()
        return f"**已選取: 0 張**", gr.update(label=_dl_btn_label(0))

    def on_prompt_json_view():
        path = _state["selected_path"]
        if not path:
            return "（尚未選擇圖片）"
        data = extract_metadata(path).get("comfyui", {}).get("prompt", {})
        return json.dumps(data, ensure_ascii=False, indent=2) if data else "（沒有 Prompt 資料）"

    def on_workflow_json_view():
        path = _state["selected_path"]
        if not path:
            return "（尚未選擇圖片）"
        data = extract_metadata(path).get("comfyui", {}).get("workflow", {})
        return json.dumps(data, ensure_ascii=False, indent=2) if data else "（沒有 Workflow 資料）"

    def on_set_cmp_target(slot_name):
        """Return the currently selected image name and path to fill comparison dropdown and icon."""
        path = _state["selected_path"]
        if not path:
            return gr.update(), gr.update()
        name = Path(str(path)).name
        # Keep the slot_name text (e.g. "🔍 比對 A") rather than replacing it with `name`
        return gr.update(value=name), gr.update(value=slot_name, icon=path)

    # ── UI ──────────────────────────────────────

    compareA=gr.Button("<", size="sm", scale=1, min_width=0)
    compareB=gr.Button(">", size="sm", scale=1, min_width=0)
    with gr.Blocks(title="ComfyUI 媒體管理器") as demo:

        # ── Top header: title + controls ────────────────────────
        with gr.Row(elem_id="top-bar"):
            with gr.Column(scale=1, min_width=160):
                gr.HTML("<h3 style='margin:0; font-size:1rem; white-space:nowrap;'>🎨 ComfyUI 媒體管理器</h3>")
            with gr.Column(scale=4, min_width=0):
                with gr.Row():
                    folder_input = gr.Dropdown(
                        choices=DEFAULT_FOLDERS,
                        value=DEFAULT_FOLDER,
                        show_label=False,
                        allow_custom_value=True,
                        scale=5,
                        container=False,
                    )
                    refresh_btn = gr.Button("🔄 掃描", variant="primary", scale=1, size="sm")
                    filter_radio = gr.Dropdown(
                        choices=["all", "image", "video"],
                        value="all",
                        show_label=False,
                        scale=1,
                        container=False,
                        min_width=80,
                    )
                    search_box = gr.Textbox(
                        show_label=False,
                        placeholder="🔍 搜尋...",
                        scale=2,
                        container=False,
                    )

        with gr.Tabs():

            # ═══════════════════════════════════════
            # Tab 1: Browse — Sidebar layout
            # ═══════════════════════════════════════
            with gr.Tab("🗂️ 瀏覽"):
                with gr.Row(equal_height=False):

                    # ── Left sidebar ──────────────
                    with gr.Column(scale=1, min_width=240, elem_classes="sidebar"):

                        title_txt = gr.Markdown("#### 🔎 詳情")

                        detail_image = gr.Image(
                            show_label=False, height=300, interactive=False,buttons=["download", compareA, compareB]
                        )
                        detail_video = gr.Video(show_label=False, height=300, visible=False)


                        with gr.Row():
                            cmp_a_btn = gr.Button("🔍 比對 A", variant="secondary", size="lg", scale=1, min_width=0, elem_id="cmp-a-btn")
                            cmp_b_btn = gr.Button("🔍 比對 B", variant="secondary", size="lg", scale=1, min_width=0, elem_id="cmp-b-btn")
                        with gr.Row():
                            selection_count_main = gr.Markdown("已選取: `0`", elem_classes="selection-count")

                        with gr.Accordion("🤖 提示詞", open=True):
                            detail_prompt = gr.Markdown()
                            
                        with gr.Accordion("📋 元資料", open=True):
                            detail_meta = gr.Markdown(elem_classes="meta-panel")

                        with gr.Accordion("🔄 Workflow JSON", open=False):
                            workflow_json_btn = gr.Button("🔍 開彈窗檢視 Workflow JSON", size="sm", variant="secondary")
                            workflow_json_hidden = gr.Textbox(visible=False, label="")

                    # ── Right: Gallery ────────────
                    with gr.Column(scale=4, elem_classes="main-gallery"):
                        with gr.Row():
                            select_all_btn = gr.Button("☑️ 全選", size="sm")
                            deselect_all_btn = gr.Button("🔲 全不選", size="sm")
                            filter_sel_btn = gr.Button("🔍 只看已選", size="sm", elem_id="filter-sel-btn")
                            batch_fav_btn = gr.Button("❤️ 批量收藏", variant="secondary", size="sm")
                            batch_dl_btn = gr.DownloadButton("⬇️ 批量下載", variant="secondary", size="sm")
                            batch_del_btn = gr.Button("🗑️ 批量刪除", variant="stop", size="sm")

                        gallery = gr.HTML(elem_id="main-gallery")
                        hidden_main_btn = gr.Button(elem_id="hidden-main-btn", elem_classes="hidden-btn")


            # ═══════════════════════════════════════
            # Tab 2: Favorites
            # ═══════════════════════════════════════
            with gr.Tab("❤️ 收藏"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=270, elem_classes="sidebar"):
                        fav_title_txt = gr.Markdown("#### 🔎 詳情")
                        fav_detail_image = gr.Image(show_label=False, height=300, interactive=False)
                        fav_detail_video = gr.Video(show_label=False, height=300, visible=False)
                        with gr.Row():
                            selection_count_fav = gr.Markdown("已選取: `0`", elem_classes="selection-count")
                        with gr.Row():
                            fav_fav_btn = gr.Button("❤️ 取消收藏", variant="secondary", size="sm")
                            fav_dl_btn = gr.DownloadButton("⬇️ 下載", variant="secondary", size="sm")
                        with gr.Accordion("📋 元資料", open=True):
                            fav_detail_meta = gr.Markdown(elem_classes="meta-panel")
                        with gr.Accordion("🤖 提示詞", open=False):
                            fav_detail_prompt = gr.Markdown()

                    with gr.Column(scale=4):
                        with gr.Row():
                            fav_select_all_btn = gr.Button("☑️ 全選", size="sm")
                            fav_deselect_all_btn = gr.Button("🔲 全不選", size="sm")
                            fav_batch_dl_btn = gr.DownloadButton("⬇️ 批量下載", variant="secondary", size="sm")
                            fav_batch_remove_btn = gr.Button("🗑️ 批量取消收藏", variant="stop", size="sm")

                        fav_gallery = gr.HTML(elem_id="fav-gallery")
                        hidden_fav_btn = gr.Button(elem_id="hidden-fav-btn", elem_classes="hidden-btn")

            # ═══════════════════════════════════════
            # Tab 3: Compare
            # ═══════════════════════════════════════
            with gr.Tab("🔍 圖片比對"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=270, elem_classes="sidebar"):
                        cmp_select_a = gr.Dropdown(label="🖼️ 圖片 A", choices=[])
                        cmp_select_b = gr.Dropdown(label="🖼️ 圖片 B", choices=[])
                        cmp_btn = gr.Button("⚡ 比對！", variant="primary")
                        
                        gr.HTML("<hr style='margin: 15px 0; border-color: #e2e8f0;'>")
                        gr.Markdown("#### 圖片 A")
                        cmp_meta_a = gr.Markdown(elem_classes="meta-panel")
                        gr.HTML("<hr style='margin: 15px 0; border-color: #e2e8f0;'>")
                        gr.Markdown("#### 圖片 B")
                        cmp_meta_b = gr.Markdown(elem_classes="meta-panel")

                    with gr.Column(scale=4):
                        cmp_slider = gr.ImageSlider(
                            label="拖動中線比對",
                            show_label=False,
                            type="filepath",
                            interactive=True,
                            height=820,
                            elem_id="cmp-slider"
                        )

            # ═══════════════════════════════════════
            # Tab 4: Settings
            # ═══════════════════════════════════════
            with gr.Tab("⚙️ 設定"):
                with gr.Column(scale=1, min_width=400):
                    gr.Markdown("## ⚙️ 工具設定")
                    gr.Markdown("---")

                    gr.Markdown("### 🔍 提示詞提取關鍵字")
                    gr.Markdown(
                        "ComfyUI workflow 中，符合此 `_meta.title` 的節點文字將被優先顯示為提示詞。\n\n"
                        "例如：節點 `ShowText|pysssss` 若 title 設為 `genPrompts`，其 `text_0` 欄位即為提示詞。"
                    )
                    prompt_keyword_input = gr.Textbox(
                        label="提示詞節點關鍵字",
                        value=load_settings().get("prompt_keyword", "genPrompts"),
                        placeholder="genPrompts",
                        max_lines=1,
                        info="大小寫敏感，需與 ComfyUI 節點的 title 完全一致"
                    )
                    save_settings_btn = gr.Button("💾 儲存設定", variant="primary", size="lg")
                    settings_status = gr.Markdown("")

        # ── Events ──────────────────────────────────

        # Gallery → detail outputs needed for refresh reset
        _do = [detail_meta, detail_prompt, detail_image, detail_video, title_txt]

        # Settings save
        def on_save_settings(keyword: str):
            from utils.metadata import get_prompt_text_quick
            kw = keyword.strip() or "genPrompts"
            save_settings({"prompt_keyword": kw})
            get_prompt_text_quick.cache_clear()  # invalidate lru_cache
            return f"✅ 已儲存！關鍵字設為：`{kw}`"

        save_settings_btn.click(
            on_save_settings,
            inputs=[prompt_keyword_input],
            outputs=[settings_status]
        )

        # Refresh
        _ri = [folder_input, filter_radio, search_box]
        # _ro has 5 + 5 + 1 = 11 elements
        _ro = [gallery, cmp_select_a, cmp_select_b, cmp_a_btn, cmp_b_btn] + _do
        refresh_btn.click(
            refresh_gallery, 
            inputs=_ri, 
            outputs=_ro + [selection_count_main]
        )
        folder_input.change(refresh_gallery, _ri, _ro + [selection_count_main])
        filter_radio.change(refresh_gallery, _ri, _ro + [selection_count_main])
        search_box.submit(refresh_gallery, _ri, _ro + [selection_count_main])

        # Delete file and Batch Delete
        js_confirm_delete = """
        function(folder, filter_type, search) {
            if (!confirm("確定要在磁碟中永久刪除這個檔案嗎？\\n(注意：此操作無法還原)")) {
                return ["CANCEL", filter_type, search];
            }
            return [folder, filter_type, search];
        }
        """
        
        js_batch_confirm_delete = """
        function(folder, filter, search) {
            let sel = document.querySelectorAll('#main-gallery .cb-item.selected');
            if (sel.length === 0) {
                alert('請先選擇檔案');
                return ["CANCEL", filter, search];
            }
            let deletable = Array.from(sel).filter(el => !el.dataset.favorited);
            if (deletable.length === 0) {
                alert('所選檔案均為收藏項目，無法刪除。\\n請先取消收藏後再刪除。');
                return ["CANCEL", filter, search];
            }
            let skipped = sel.length - deletable.length;
            let msg = `確定要在磁碟中永久刪除這 ${deletable.length} 個檔案嗎？\\n(注意：此操作無法還原)`;
            if (skipped > 0) msg += `\\n（已跳過 ${skipped} 個收藏檔案）`;
            if (!confirm(msg)) {
                return ["CANCEL", filter, search];
            }
            return [folder, filter, search];
        }
        """


        def on_delete_file(folder, filter_type, search):
            if folder == "CANCEL":
                return tuple(gr.update() for _ in range(len(_ro)))
            
            path = _state["selected_path"]
            if path and Path(str(path)).exists():
                try:
                    Path(str(path)).unlink()
                    _state["selected_path"] = ""
                    _state["selected_batch"].discard(path)
                except Exception as e:
                    print(f"Delete file failed: {e}")
            else:
                return tuple(gr.update() for _ in range(len(_ro)))
            
            return refresh_gallery(folder, filter_type, search)


        select_all_btn.click(
            on_select_all_main, 
            outputs=[selection_count_main, batch_dl_btn],
            js="() => { document.querySelectorAll('#main-gallery .cb-item').forEach(el => el.classList.add('selected')); return undefined; }"
        )
        deselect_all_btn.click(
            on_deselect_all_main, 
            outputs=[selection_count_main, batch_dl_btn],
            js="() => { document.querySelectorAll('#main-gallery .cb-item').forEach(el => el.classList.remove('selected')); return undefined; }"
        )
        batch_fav_btn.click(on_batch_fav_main, outputs=[gallery])
        batch_dl_btn.click(on_batch_download_main, outputs=[batch_dl_btn])
        batch_del_btn.click(
            on_batch_delete_main,
            inputs=_ri,
            outputs=_ro + [selection_count_main],
            js=js_batch_confirm_delete
        )

        _open_json_js = """(json) => {
            if (!json || json.startsWith('\uff08')) return;
            var existing = document.getElementById('_json_modal');
            if (existing) existing.remove();
            var modal = document.createElement('div');
            modal.id = '_json_modal';
            Object.assign(modal.style, {
                position:'fixed', inset:'0', zIndex:'999999',
                background:'rgba(0,0,0,0.65)', display:'flex',
                alignItems:'center', justifyContent:'center'
            });
            var box = document.createElement('div');
            Object.assign(box.style, {
                background:'#1e1e2e', borderRadius:'12px',
                width:'80vw', maxWidth:'900px', maxHeight:'80vh',
                display:'flex', flexDirection:'column',
                boxShadow:'0 8px 40px rgba(0,0,0,0.6)',
                overflow:'hidden'
            });
            var header = document.createElement('div');
            Object.assign(header.style, {
                display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'10px 16px', background:'#181825',
                borderBottom:'1px solid #333', flexShrink:'0'
            });
            var title = document.createElement('span');
            title.textContent = 'JSON';
            title.style.cssText = 'color:#cdd6f4;font-family:monospace;font-size:14px;font-weight:600';
            var dlBtn = document.createElement('button');
            dlBtn.textContent = '⬇️ 下載';
            dlBtn.style.cssText = 'background:rgba(79,70,229,0.7);border:none;color:#fff;font-size:13px;cursor:pointer;padding:4px 12px;border-radius:8px;margin-left:auto;margin-right:8px';
            dlBtn.onclick = function(){
                var b = new Blob([json], {type:'application/json'});
                var a = document.createElement('a');
                a.href = URL.createObjectURL(b);
                a.download = 'data.json';
                a.click();
            };
            var closeBtn = document.createElement('button');
            closeBtn.textContent = '✕';
            closeBtn.style.cssText = 'background:none;border:none;color:#cdd6f4;font-size:18px;cursor:pointer;padding:2px 8px;border-radius:6px';
            closeBtn.onclick = function(){ modal.remove(); };
            header.appendChild(title);
            header.appendChild(dlBtn);
            header.appendChild(closeBtn);
            var pre = document.createElement('pre');
            Object.assign(pre.style, {
                margin:'0', padding:'16px', overflowY:'auto',
                fontSize:'12px', lineHeight:'1.6',
                fontFamily:'monospace', color:'#cdd6f4',
                whiteSpace:'pre-wrap', wordBreak:'break-all'
            });
            pre.textContent = json;
            box.appendChild(header);
            box.appendChild(pre);
            modal.appendChild(box);
            modal.onclick = function(e){ if(e.target === modal) modal.remove(); };
            document.body.appendChild(modal);
        }"""
        workflow_json_btn.click(on_workflow_json_view, inputs=[], outputs=[workflow_json_hidden]).then(
            fn=None, inputs=[workflow_json_hidden], js=_open_json_js
        )

        # Triggered from custom Javascript via Button click
        hidden_main_btn.click(
            on_main_click, 
            inputs=[folder_input], # Dummy input required for python parameter mapping
            outputs=_do + [selection_count_main, batch_dl_btn],
            js="(dummy) => { return window._comfy_selected_main || ''; }"
        )

        # Folder picker
        # Compare shortcut buttons from Browse tab
        cmp_a_btn.click(lambda: on_set_cmp_target("🔍 比對 A"), outputs=[cmp_select_a, cmp_a_btn])
        cmp_b_btn.click(lambda: on_set_cmp_target("🔍 比對 B"), outputs=[cmp_select_b, cmp_b_btn])
        
        compareA.click(lambda: on_set_cmp_target("🔍 比對 A"), outputs=[cmp_select_a, cmp_a_btn])
        compareB.click(lambda: on_set_cmp_target("🔍 比對 B"), outputs=[cmp_select_b, cmp_b_btn])
        # Favorites tab — _fo maps to on_fav_click returns: (meta, prompt, fav_btn, img, video, title)
        _fo = [fav_detail_meta, fav_detail_prompt, fav_fav_btn, fav_detail_image, fav_detail_video, fav_title_txt]
        fav_fav_btn.click(on_fav_toggle, outputs=[fav_fav_btn, fav_gallery])
        fav_dl_btn.click(on_download, outputs=[fav_dl_btn])

        fav_select_all_btn.click(
            on_select_all_fav, 
            outputs=[selection_count_fav, fav_batch_dl_btn],
            js="() => { document.querySelectorAll('#fav-gallery .cb-item').forEach(el => el.classList.add('selected')); return undefined; }"
        )
        fav_deselect_all_btn.click(
            on_deselect_all_fav, 
            outputs=[selection_count_fav, fav_batch_dl_btn],
            js="() => { document.querySelectorAll('#fav-gallery .cb-item').forEach(el => el.classList.remove('selected')); return undefined; }"
        )
        fav_batch_dl_btn.click(on_batch_download_fav, outputs=[fav_batch_dl_btn])
        fav_batch_remove_btn.click(on_fav_batch_remove, outputs=[fav_gallery, selection_count_fav]) # Also reset counter
        
        # Triggered from custom Javascript via Button click
        hidden_fav_btn.click(
            on_fav_click, 
            inputs=[folder_input], # Dummy input required for python parameter mapping
            outputs=_fo + [selection_count_fav, fav_batch_dl_btn],
            js="(dummy) => { return window._comfy_selected_fav || ''; }"
        )

        # Compare
        cmp_btn.click(on_compare, [cmp_select_a, cmp_select_b],
                      [cmp_slider, cmp_meta_a, cmp_meta_b])

        gallery_js = """
        function() {
            // ── Build global path→prompt map ──────────────
            window._comfy_prompt_map = {};
            function rebuildPromptMap() {
                window._comfy_prompt_map = {};
                document.querySelectorAll('.cb-item[data-prompt]').forEach(function(el) {
                    var p = el.getAttribute('data-path');
                    var pt = el.getAttribute('data-prompt');
                    if (p && pt) window._comfy_prompt_map[p] = pt;
                });
            }
            rebuildPromptMap();
            var _galleryEl = document.querySelector('#main-gallery');
            if (_galleryEl) {
                new MutationObserver(function(muts) {
                    if (muts.some(function(m){ return m.addedNodes.length > 0; }))
                        rebuildPromptMap();
                }).observe(_galleryEl, {childList: true, subtree: true});
            }

            // ── Filter: show only selected ───────────────
            var _filterActive = false;

            function applyFilter() {
                document.querySelectorAll('#main-gallery .cb-item').forEach(function(el) {
                    el.style.display = (_filterActive && !el.classList.contains('selected')) ? 'none' : '';
                });
            }

            // Listen for filter button via event delegation
            document.addEventListener('click', function(e) {
                var btn = e.target.closest('#filter-sel-btn button, #filter-sel-btn');
                if (btn) {
                    _filterActive = !_filterActive;
                    // Update button label
                    var span = btn.querySelector('span') || btn;
                    if (span.textContent.includes('只看已選') || span.textContent.includes('顯示全部')) {
                        span.textContent = _filterActive ? '🟢 顯示全部' : '🔍 只看已選';
                    }
                    applyFilter();
                }
            });

            // Document-level click listener for selection
            document.addEventListener('click', function(e) {
                var item = e.target.closest('.cb-item');
                if (item) {
                    var type = item.getAttribute('data-type');
                    var path = item.getAttribute('data-path');
                    if (!type || !path) return;
                    
                    // visual feedback immediately
                    item.classList.toggle('selected');
                    // re-apply filter if active
                    if (typeof applyFilter === 'function') applyFilter();
                    
                    if (type === 'main') {
                        window._comfy_selected_main = path;
                        // Find the real button inside the Gradio wrapper block
                        var dom_btn = document.querySelector('#hidden-main-btn');
                        if (dom_btn) {
                            var real_btn = dom_btn.tagName === 'BUTTON' ? dom_btn : dom_btn.querySelector('button');
                            if (real_btn) real_btn.click();
                        }
                    } else if (type === 'fav') {
                        window._comfy_selected_fav = path;
                        var dom_btn = document.querySelector('#hidden-fav-btn');
                        if (dom_btn) {
                            var real_btn = dom_btn.tagName === 'BUTTON' ? dom_btn : dom_btn.querySelector('button');
                            if (real_btn) real_btn.click();
                        }
                    }
                }
            });

            // Document-level double-click listener for fullscreen preview
            document.addEventListener('dblclick', function(e) {
                var item = e.target.closest('.cb-item');
                if (!item) return;
                var img = item.querySelector('img.cb-image');
                if (!img) return; // skip video placeholders

                e.preventDefault();
                // Rebuild prompt map to ensure latest data-prompt values
                if (typeof rebuildPromptMap === "function") rebuildPromptMap();

                // Collect all image items from the same gallery container
                var gallery = item.closest('.cb-wrap');
                var allItems = gallery ? Array.from(gallery.querySelectorAll('.cb-item img.cb-image')) : [img];
                var currentIdx = allItems.indexOf(img);

                function getItemPath(imgEl) {
                    var cb = imgEl.closest('.cb-item');
                    return cb ? cb.getAttribute('data-path') : '';
                }

                function getCaption(imgEl) {
                    var cb = imgEl.closest('.cb-item');
                    var cap = cb ? cb.querySelector('.cb-caption') : null;
                    if (!cap) return '';
                    var t = document.createElement('div');
                    t.innerHTML = cap.innerHTML.replace(/<br>/gi, ' | ');
                    return t.textContent || t.innerText;
                }

                var currentPath = getItemPath(img);

                // ── 提示詞面板 ──
                function getItemPrompt(imgEl) {
                    var cb = imgEl.closest('.cb-item');
                    var path = cb ? cb.getAttribute('data-path') : '';
                    return (path && window._comfy_prompt_map && window._comfy_prompt_map[path]) || '';
                }

                var currentPrompt = getItemPrompt(img);
                var _promptVisible = false;

                var promptPanel = document.createElement('div');
                Object.assign(promptPanel.style, {
                    display: 'none',
                    position: 'absolute', top: '0', left: '0', bottom: '0',
                    width: '300px', maxWidth: '38vw',
                    overflowY: 'auto',
                    background: 'linear-gradient(to right, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0) 100%)',
                    color: '#fff', fontSize: '13px', lineHeight: '1.8',
                    fontFamily: 'sans-serif', padding: '70px 32px 24px 20px',
                    whiteSpace: 'pre-wrap', zIndex: '100010',
                    textShadow: '0 1px 3px rgba(0,0,0,0.9), 0 0 6px rgba(0,0,0,0.7)',
                    pointerEvents: 'none'
                });

                var promptToggleBtn = document.createElement('div');
                Object.assign(promptToggleBtn.style, {
                    position: 'absolute', top: '14px', left: '20px',
                    background: 'rgba(100,100,100,0.5)',
                    border: '2px solid rgba(255,255,255,0.3)',
                    borderRadius: '20px', padding: '4px 14px',
                    color: 'rgba(255,255,255,0.6)', fontSize: '12px',
                    fontFamily: 'sans-serif', cursor: 'pointer',
                    userSelect: 'none', zIndex: '100003',
                    whiteSpace: 'nowrap', transition: 'background 0.2s'
                });
                promptToggleBtn.textContent = '📝 提示詞';

                promptToggleBtn.onclick = function(ev) {
                    ev.stopPropagation();
                    _promptVisible = !_promptVisible;
                    if (_promptVisible) {
                        promptPanel.textContent = currentPrompt || '（此圖片無提示詞資料）';
                        promptPanel.style.display = 'block';
                        promptToggleBtn.style.background = 'rgba(79,70,229,0.7)';
                        promptToggleBtn.style.borderColor = 'rgba(255,255,255,0.6)';
                        promptToggleBtn.style.color = '#fff';
                        promptToggleBtn.textContent = '📝 隱藏提示詞';
                    } else {
                        promptPanel.style.display = 'none';
                        promptToggleBtn.style.background = 'rgba(100,100,100,0.5)';
                        promptToggleBtn.style.borderColor = 'rgba(255,255,255,0.3)';
                        promptToggleBtn.style.color = 'rgba(255,255,255,0.6)';
                        promptToggleBtn.textContent = '📝 提示詞';
                    }
                };

                function cmpBtnStyle(side) {
                    return {
                        position: 'absolute',
                        bottom: '90px',
                        [side]: '20px',
                        background: 'rgba(79,70,229,0.75)',
                        border: '2px solid rgba(255,255,255,0.6)',
                        borderRadius: '24px',
                        padding: '6px 14px',
                        color: '#fff', fontSize: '13px', fontWeight: '600',
                        fontFamily: 'sans-serif',
                        cursor: 'pointer', userSelect: 'none',
                        zIndex: '100002',
                        transition: 'background 0.2s',
                        whiteSpace: 'nowrap'
                    };
                }

                var cmpABtn = document.createElement('div');
                cmpABtn.textContent = '🔍 加入比對 A';
                Object.assign(cmpABtn.style, cmpBtnStyle('left'));
                cmpABtn.onmouseenter = function() { cmpABtn.style.background = 'rgba(79,70,229,1)'; };
                cmpABtn.onmouseleave = function() { cmpABtn.style.background = 'rgba(79,70,229,0.75)'; };

                var cmpBBtn = document.createElement('div');
                cmpBBtn.textContent = '🔍 加入比對 B';
                Object.assign(cmpBBtn.style, cmpBtnStyle('right'));
                cmpBBtn.onmouseenter = function() { cmpBBtn.style.background = 'rgba(79,70,229,1)'; };
                cmpBBtn.onmouseleave = function() { cmpBBtn.style.background = 'rgba(79,70,229,0.75)'; };

                function triggerCmp(cmpElemId) {
                    if (!currentPath) return;
                    // 記錄比對A路徑供比對模式使用
                    if (cmpElemId === '#cmp-a-btn') {
                        window._comfy_cmp_a_src = currentPath;
                        updateCmpModeBtn();
                    }
                    // 1. Update server state
                    window._comfy_selected_main = currentPath;
                    var hiddenBtn = document.querySelector('#hidden-main-btn button, #hidden-main-btn');
                    if (hiddenBtn && hiddenBtn.tagName !== 'BUTTON') hiddenBtn = hiddenBtn.querySelector('button');
                    if (hiddenBtn) hiddenBtn.click();
                    // 2. After state is updated, click the sidebar cmp button
                    setTimeout(function() {
                        var cmpBtn = document.querySelector(cmpElemId + ' button, ' + cmpElemId);
                        if (cmpBtn && cmpBtn.tagName !== 'BUTTON') cmpBtn = cmpBtn.querySelector('button');
                        if (cmpBtn) cmpBtn.click();
                    }, 400);
                }

                cmpABtn.onclick = function(ev) { ev.stopPropagation(); triggerCmp('#cmp-a-btn'); };
                cmpBBtn.onclick = function(ev) { ev.stopPropagation(); triggerCmp('#cmp-b-btn'); };

                // ── 比對模式按鈕（頂端右邊）──
                var cmpModeBtn = document.createElement('div');
                Object.assign(cmpModeBtn.style, {
                    position: 'absolute', top: '14px', right: '20px',
                    background: 'rgba(100,100,100,0.5)',
                    border: '2px solid rgba(255,255,255,0.3)',
                    borderRadius: '20px', padding: '4px 14px',
                    color: 'rgba(255,255,255,0.4)', fontSize: '12px',
                    fontFamily: 'sans-serif', cursor: 'default',
                    userSelect: 'none', zIndex: '100003',
                    whiteSpace: 'nowrap', transition: 'background 0.2s'
                });
                cmpModeBtn.textContent = '📊 比對模式';

                var _cmpModeActive = false;

                function updateCmpModeBtn() {
                    var hasA = !!window._comfy_cmp_a_src;
                    cmpModeBtn.style.cursor = hasA ? 'pointer' : 'default';
                    cmpModeBtn.style.color = hasA ? '#fff' : 'rgba(255,255,255,0.35)';
                    cmpModeBtn.style.background = hasA ? 'rgba(79,70,229,0.7)' : 'rgba(100,100,100,0.5)';
                    cmpModeBtn.style.borderColor = hasA ? 'rgba(255,255,255,0.6)' : 'rgba(255,255,255,0.2)';
                    cmpModeBtn.textContent = _cmpModeActive ? '🖼️ 單圖模式' : '📊 比對模式';
                }
                updateCmpModeBtn();

                // ── 並排比對視圖 ──
                // ── ImageSlider 重疊比對容器 ──
                var cmpContainer = document.createElement('div');
                Object.assign(cmpContainer.style, {
                    display: 'none', position: 'absolute', inset: '0',
                    overflow: 'hidden', cursor: 'col-resize'
                });

                // 底層圖：比對A（完整顯示）
                var sliderImgA = document.createElement('img');
                Object.assign(sliderImgA.style, {
                    position: 'absolute', inset: '0',
                    width: '100%', height: '100%',
                    objectFit: 'contain',
                    userSelect: 'none', pointerEvents: 'none'
                });

                // 上層圖：目前圖（用 clip-path 裁切右半）
                var sliderImgB = document.createElement('img');
                Object.assign(sliderImgB.style, {
                    position: 'absolute', inset: '0',
                    width: '100%', height: '100%',
                    objectFit: 'contain',
                    userSelect: 'none', pointerEvents: 'none',
                    clipPath: 'inset(0 50% 0 0)'
                });

                // 分隔線
                var sliderLine = document.createElement('div');
                Object.assign(sliderLine.style, {
                    position: 'absolute', top: '0', bottom: '0',
                    left: '50%', width: '3px',
                    background: 'rgba(255,255,255,0.9)',
                    transform: 'translateX(-50%)',
                    pointerEvents: 'none', zIndex: '10'
                });

                // 拖把圓鈕
                var sliderHandle = document.createElement('div');
                Object.assign(sliderHandle.style, {
                    position: 'absolute', top: '50%', left: '50%',
                    width: '40px', height: '40px',
                    transform: 'translate(-50%, -50%)',
                    background: '#fff', borderRadius: '50%',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.5)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '16px', cursor: 'col-resize',
                    userSelect: 'none', zIndex: '11'
                });
                sliderHandle.textContent = '◀▶';

                // 標籤：左下（A）右下（目前圖）
                var sliderLabelA = document.createElement('div');
                Object.assign(sliderLabelA.style, {
                    position: 'absolute', bottom: '20px', left: '16px',
                    background: 'rgba(79,70,229,0.85)', color: '#fff',
                    padding: '3px 12px', borderRadius: '12px',
                    fontSize: '12px', fontFamily: 'sans-serif',
                    pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: '12'
                });

                var sliderLabelB = document.createElement('div');
                Object.assign(sliderLabelB.style, {
                    position: 'absolute', bottom: '20px', right: '16px',
                    background: 'rgba(0,0,0,0.65)', color: '#fff',
                    padding: '3px 12px', borderRadius: '12px',
                    fontSize: '12px', fontFamily: 'sans-serif',
                    pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: '12'
                });

                var _dragging = false;
                var _sliderPct = 50;

                function setSlider(pct) {
                    _sliderPct = Math.min(95, Math.max(5, pct));
                    sliderImgB.style.clipPath = 'inset(0 0 0 ' + _sliderPct.toFixed(1) + '%)';
                    sliderLine.style.left = _sliderPct + '%';
                    sliderHandle.style.left = _sliderPct + '%';
                }

                function onDragMove(clientX) {
                    if (!_dragging || !_cmpModeActive) return;
                    var rect = cmpContainer.getBoundingClientRect();
                    setSlider((clientX - rect.left) / rect.width * 100);
                }

                cmpContainer.addEventListener('mousedown', function(ev) {
                    _dragging = true; ev.preventDefault();
                });
                document.addEventListener('mousemove', function(ev) { onDragMove(ev.clientX); });
                document.addEventListener('mouseup', function() { _dragging = false; });

                // Touch 支援
                cmpContainer.addEventListener('touchstart', function(ev) {
                    _dragging = true; ev.preventDefault();
                }, { passive: false });
                document.addEventListener('touchmove', function(ev) {
                    if (ev.touches[0]) onDragMove(ev.touches[0].clientX);
                });
                document.addEventListener('touchend', function() { _dragging = false; });

                cmpContainer.appendChild(sliderImgA);
                cmpContainer.appendChild(sliderImgB);
                cmpContainer.appendChild(sliderLine);
                cmpContainer.appendChild(sliderHandle);
                cmpContainer.appendChild(sliderLabelA);
                cmpContainer.appendChild(sliderLabelB);

                function enterCmpMode() {
                    if (!window._comfy_cmp_a_src) return;
                    _cmpModeActive = true;
                    sliderImgA.src = '/gradio_api/file=' + window._comfy_cmp_a_src;
                    sliderImgB.src = media.src;
                    sliderLabelA.textContent = '◀ A: ' + window._comfy_cmp_a_src.split('/').pop();
                    sliderLabelB.textContent = '▶ ' + (currentPath ? currentPath.split('/').pop() : '目前圖');
                    setSlider(50);  // 初始 50%
                    cmpContainer.style.display = 'block';
                    media.style.display = 'none';
                    label.style.display = 'none';
                    // 比對模式中仍顯示切換按鈕（縮小置於底部），但隱藏「加入比對」按鈕
                    if (allItems.length > 1) {
                        prevBtn.style.display = 'block';
                        nextBtn.style.display = 'block';
                        // 移到底部，避免遮擋比對線
                        prevBtn.style.top = 'auto';
                        prevBtn.style.bottom = '60px';
                        nextBtn.style.top = 'auto';
                        nextBtn.style.bottom = '60px';
                        prevBtn.style.transform = 'none';
                        nextBtn.style.transform = 'none';
                    }
                    cmpABtn.style.display = 'none';
                    cmpBBtn.style.display = 'none';
                    updateCmpModeBtn();
                }

                function exitCmpMode() {
                    _cmpModeActive = false;
                    cmpContainer.style.display = 'none';
                    media.style.display = '';
                    label.style.display = '';
                    var numItems = allItems.length;
                    prevBtn.style.display = numItems > 1 ? 'block' : 'none';
                    nextBtn.style.display = numItems > 1 ? 'block' : 'none';
                    // 還原切換按鈕到中間位置
                    prevBtn.style.top = '50%';
                    prevBtn.style.bottom = 'auto';
                    nextBtn.style.top = '50%';
                    nextBtn.style.bottom = 'auto';
                    prevBtn.style.transform = 'translateY(-50%)';
                    nextBtn.style.transform = 'translateY(-50%)';
                    cmpABtn.style.display = 'block';
                    cmpBBtn.style.display = 'block';
                    updateCmpModeBtn();
                }

                cmpModeBtn.onclick = function(ev) {
                    ev.stopPropagation();
                    if (!window._comfy_cmp_a_src) return;
                    if (_cmpModeActive) exitCmpMode(); else enterCmpMode();
                };


                // Build overlay container
                var overlay = document.createElement('div');
                overlay.id = 'custom-fullscreen-overlay';
                Object.assign(overlay.style, {
                    position: 'fixed', top: '0', left: '0',
                    width: '100vw', height: '100vh',
                    backgroundColor: 'rgba(0,0,0,0.88)',
                    zIndex: '99999', display: 'flex',
                    justifyContent: 'center', alignItems: 'center'
                });

                var media = document.createElement('img');
                Object.assign(media.style, {
                    maxWidth: '88%', maxHeight: '88vh',
                    objectFit: 'contain', transition: 'opacity 0.15s',
                    pointerEvents: 'none', userSelect: 'none'
                });

                var label = document.createElement('div');
                Object.assign(label.style, {
                    position: 'absolute', bottom: '36px', left: '50%',
                    transform: 'translateX(-50%)',
                    color: '#fff', backgroundColor: 'rgba(0,0,0,0.55)',
                    padding: '6px 18px', borderRadius: '20px',
                    fontSize: '13px', fontFamily: 'sans-serif',
                    pointerEvents: 'none', whiteSpace: 'nowrap',
                    maxWidth: '80vw', overflow: 'hidden', textOverflow: 'ellipsis'
                });

                function navBtnStyle(side) {
                    return {
                        position: 'absolute', top: '50%',
                        [side]: '20px',
                        transform: 'translateY(-50%)',
                        background: 'rgba(255,255,255,0.15)',
                        border: '2px solid rgba(255,255,255,0.5)',
                        borderRadius: '50%', width: '52px', height: '52px',
                        color: '#fff', fontSize: '26px', lineHeight: '48px',
                        textAlign: 'center', cursor: 'pointer',
                        userSelect: 'none', zIndex: '100001',
                        transition: 'background 0.2s'
                    };
                }

                var prevBtn = document.createElement('div');
                prevBtn.textContent = '‹';
                Object.assign(prevBtn.style, navBtnStyle('left'));
                prevBtn.onmouseenter = function() { prevBtn.style.background = 'rgba(255,255,255,0.35)'; };
                prevBtn.onmouseleave = function() { prevBtn.style.background = 'rgba(255,255,255,0.15)'; };

                var nextBtn = document.createElement('div');
                nextBtn.textContent = '›';
                Object.assign(nextBtn.style, navBtnStyle('right'));
                nextBtn.onmouseenter = function() { nextBtn.style.background = 'rgba(255,255,255,0.35)'; };
                nextBtn.onmouseleave = function() { nextBtn.style.background = 'rgba(255,255,255,0.15)'; };

                function showImage(idx) {
                    currentIdx = (idx + allItems.length) % allItems.length;
                    currentPath = getItemPath(allItems[currentIdx]);
                    currentPrompt = getItemPrompt(allItems[currentIdx]);
                    var newSrc = allItems[currentIdx].src.split('?')[0];
                    if (_cmpModeActive) {
                        // 比對模式：更新 B 圖（目前圖）
                        sliderImgB.src = newSrc;
                        sliderLabelB.textContent = '▶ ' + (currentPath ? currentPath.split('/').pop() : '');
                        // 更新 media.src 以供退出比對模式時使用
                        media.src = newSrc;
                        label.textContent = getCaption(allItems[currentIdx]);
                    } else {
                        media.style.opacity = '0';
                        setTimeout(function() {
                            media.src = newSrc;
                            label.textContent = getCaption(allItems[currentIdx]);
                            media.style.opacity = '1';
                        }, 80);
                    }
                    // 若提示詞面板已開，即時更新內容
                    if (_promptVisible) {
                        promptPanel.textContent = currentPrompt || '（此圖片無提示詞資料）';
                    }
                    // show/hide nav buttons
                    prevBtn.style.display = allItems.length > 1 ? 'block' : 'none';
                    nextBtn.style.display = allItems.length > 1 ? 'block' : 'none';
                }

                prevBtn.onclick = function(ev) { ev.stopPropagation(); showImage(currentIdx - 1); };
                nextBtn.onclick = function(ev) { ev.stopPropagation(); showImage(currentIdx + 1); };

                overlay.appendChild(media);
                overlay.appendChild(label);
                overlay.appendChild(prevBtn);
                overlay.appendChild(nextBtn);
                overlay.appendChild(cmpABtn);
                overlay.appendChild(cmpBBtn);
                overlay.appendChild(cmpContainer);
                overlay.appendChild(cmpModeBtn);
                overlay.appendChild(promptToggleBtn);
                overlay.appendChild(promptPanel);

                // ── Hotkey hint bar (top center, auto-fade) ──
                var hint = document.createElement('div');
                hint.innerHTML = '⬅ ➡ &nbsp;切換圖片 &nbsp;｜&nbsp; <kbd style="background:rgba(255,255,255,0.2);border-radius:4px;padding:1px 5px">Esc</kbd>&nbsp; 關閉';
                Object.assign(hint.style, {
                    position: 'absolute', top: '14px', left: '50%',
                    transform: 'translateX(-50%)',
                    color: 'rgba(255,255,255,0.85)',
                    backgroundColor: 'rgba(0,0,0,0.45)',
                    backdropFilter: 'blur(4px)',
                    padding: '5px 18px', borderRadius: '20px',
                    fontSize: '12px', fontFamily: 'sans-serif',
                    pointerEvents: 'none', whiteSpace: 'nowrap',
                    zIndex: '100003'
                });
                overlay.appendChild(hint);

                function closeOverlay() {
                    if (document.body.contains(overlay)) document.body.removeChild(overlay);
                    document.removeEventListener('keydown', keyHandler);
                }

                var keyHandler = function(ev) {
                    if (ev.key === 'Escape') { closeOverlay(); }
                    else if (ev.key === 'ArrowLeft')  { showImage(currentIdx - 1); }
                    else if (ev.key === 'ArrowRight') { showImage(currentIdx + 1); }
                };
                document.addEventListener('keydown', keyHandler);

                overlay.onclick = function(ev) {
                    // Click on backdrop no longer closes overlay
                };

                // Prevent scroll from leaking to background page
                overlay.addEventListener('wheel', function(ev) {
                    ev.preventDefault();
                }, { passive: false });

                document.body.appendChild(overlay);
                showImage(currentIdx);
            });
        }
        """

        demo.load(lambda: _fav_gallery(), outputs=[fav_gallery], js=gallery_js)

    return demo, APP_CSS


if __name__ == "__main__":
    app, css = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        allowed_paths=["/"],
        theme=gr.themes.Default(
            primary_hue=gr.themes.colors.indigo,
            font=[gr.themes.GoogleFont("Inter"), "sans-serif"],
        ),
        css=css,
    )
