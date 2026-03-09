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
from utils.favorites import load_favorites, toggle_favorite, is_favorite
from utils.metadata import extract_metadata, get_prompt_summary


# ──────────────────────────────────────────────
# Native folder picker via AppleScript (macOS, thread-safe)
# ──────────────────────────────────────────────

def pick_folder() -> str:
    """
    Open the native folder chooser dialog.
    Supports macOS (osascript), Windows/Linux (tkinter), and Linux (zenity fallback).
    """
    import sys
    if sys.platform == "darwin":
        script = (
            'tell application "Finder"\\n'
            '    activate\\n'
            'end tell\\n'
            'set chosen to choose folder with prompt "選擇 ComfyUI Output 資料夾"\\n'
            'POSIX path of chosen'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120
            )
            path = result.stdout.strip()
            return path.rstrip("/") if path else ""
        except Exception:
            return ""

    # Windows / Linux primary attempt via Tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        folder = filedialog.askdirectory(title="選擇 ComfyUI Output 資料夾")
        root.destroy()
        return folder if folder else ""
    except Exception:
        pass

    # Linux fallback via Zenity if Tkinter fails
    if sys.platform.startswith("linux"):
        try:
            result = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title=選擇 ComfyUI Output 資料夾"],
                capture_output=True, text=True, timeout=120
            )
            path = result.stdout.strip()
            return path if path else ""
        except Exception:
            return ""

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


def _gallery_items(files):
    items = []
    for f in files:
        res = _get_resolution(f["path"])
        caption = f"{f['name']} | {res}"
        items.append((f["path"], caption))
    return items


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

    /* Status bar */
    .status-bar { color: #4f46e5 !important; font-size: 13px !important; font-weight: 600 !important; }

    /* Compact top bar —  1~2 lines height */
    #top-bar { padding: 4px 0 !important; align-items: center !important; gap: 6px !important; }
    #top-bar h3 { margin: 0 !important; font-size: 1rem !important; }
    #top-bar input[type="text"] { height: 34px !important; min-height: 34px !important; padding: 4px 8px !important; }
    #top-bar button { height: 34px !important; min-height: 34px !important; padding: 0 10px !important; }
    #top-bar select, #top-bar .wrap { height: 34px !important; min-height: 34px !important; }
    #top-bar .block { margin: 0 !important; padding: 0 !important; }
    #top-bar .row { gap: 4px !important; align-items: center !important; flex-wrap: nowrap !important; }

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
"""


# ──────────────────────────────────────────────
# Main app
# ──────────────────────────────────────────────

# DEFAULT_FOLDER = "/Users/chinqan-mac/Downloads/照片修改"
DEFAULT_FOLDER = "/home/opo_admin/ComfyUI/output"


def build_app():
    _state = {
        "files": [],
        "selected_path": "",
    }

    # ── Business logic ──────────────────────────

    def do_pick_folder():
        chosen = pick_folder()
        return chosen if chosen else gr.update()

    def refresh_gallery(folder, filter_type, search):
        files = scan_folder(folder, filter_type, search)
        _state["files"] = files
        _state["selected_path"] = ""
        items = _gallery_items(files)
        choices = [f["name"] for f in files if f["type"] == "image"]
        
        # Clear gallery selection and reset all detail views
        gallery_update = gr.Gallery(value=items, selected_index=None)
        select_a_update = gr.update(choices=choices, value=None)
        select_b_update = gr.update(choices=choices, value=None)
        thumb_a_update = gr.update(value=None)
        thumb_b_update = gr.update(value=None)
        
        # Return them along with cleared detail view values
        return (gallery_update, select_a_update, select_b_update, thumb_a_update, thumb_b_update) + _detail_view("")

    def _detail_view(path: str):
        if not path:
            return "", "", "⭐ 收藏", gr.update(value=None, visible=False), gr.update(value=None, visible=False), "#### 🔎 詳情"
        meta = extract_metadata(path)
        summary = meta["raw_text"]
        prompt_text = get_prompt_summary(meta)
        fav_label = "💛 已收藏" if is_favorite(path) else "⭐ 收藏"
        ext = Path(path).suffix.lower()
        is_video = ext in {".mp4", ".webm", ".mov", ".avi"}
        return (
            summary,
            prompt_text,
            fav_label,
            gr.update(value=None if is_video else path, visible=not is_video),
            gr.update(value=path if is_video else None, visible=is_video),
            gr.update(value="#### 🔎 影片詳情" if is_video else "#### 🔎 圖片詳情")
        )

    def on_gallery_select(evt: gr.SelectData):
        files = _state["files"]
        if not files or evt.index >= len(files):
            return _detail_view("")
        path = files[evt.index]["path"]
        _state["selected_path"] = path
        return _detail_view(path)

    def on_fav_toggle():
        path = _state["selected_path"]
        if not path:
            return "⭐ 收藏", _fav_gallery()
        _, is_fav = toggle_favorite(path)
        return ("💛 已收藏" if is_fav else "⭐ 收藏"), _fav_gallery()

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
        return _gallery_items(fav_files)

    def on_download():
        path = _state["selected_path"]
        return path if path and Path(path).exists() else None

    def on_fav_gallery_select(evt: gr.SelectData):
        fav_files = _get_all_favorites()
        if evt.index >= len(fav_files):
            return _detail_view("")
        path = fav_files[evt.index]["path"]
        _state["selected_path"] = path
        return _detail_view(path)

    def on_compare(name_a, name_b):
        if not name_a or not name_b:
            return None, "", ""
        by_name = {f["name"]: f["path"] for f in _state["files"]}
        pa, pb = by_name.get(name_a, ""), by_name.get(name_b, "")
        meta_a = extract_metadata(pa)["raw_text"] if pa else ""
        meta_b = extract_metadata(pb)["raw_text"] if pb else ""
        # Return tuple for gr.ImageSlider
        return (pa, pb) if pa and pb else None, meta_a, meta_b

    def on_json_view():
        path = _state["selected_path"]
        if not path:
            return "（尚未選擇圖片）"
        comfy = extract_metadata(path).get("comfyui", {})
        return json.dumps(comfy, ensure_ascii=False, indent=2) if comfy else "（沒有 ComfyUI JSON 資料）"

    def on_set_cmp_target(slot_name):
        """Return the currently selected image name and path to fill comparison dropdown and icon."""
        path = _state["selected_path"]
        if not path:
            return gr.update(), gr.update()
        name = Path(path).name
        # Keep the slot_name text (e.g. "🔍 比對 A") rather than replacing it with `name`
        return gr.update(value=name), gr.update(value=slot_name, icon=path)

    # ── UI ──────────────────────────────────────

    compareA=gr.Button("<", size="sm", scale=1, min_width=0)
    compareB=gr.Button(">", size="sm", scale=1, min_width=0)
    with gr.Blocks(title="ComfyUI 媒體管理器") as demo:

        # ── Top header: title + controls ────────────────────────
        with gr.Row(elem_id="top-bar"):
            with gr.Column(scale=1, min_width=0):
                gr.Markdown("### 🎨 ComfyUI 媒體管理器")
            with gr.Column(scale=4, min_width=0):
                with gr.Row():
                    folder_input = gr.Textbox(
                        value=DEFAULT_FOLDER,
                        show_label=False,
                        placeholder="資料夾路徑...",
                        scale=5,
                        container=False,
                    )
                    pick_btn = gr.Button("📂", scale=0, min_width=40, size="sm")
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
                            fav_btn = gr.Button("⭐", variant="secondary", size="sm", scale=1, min_width=0)
                            del_btn = gr.Button("🗑️ 刪除", variant="stop", size="sm", scale=1, min_width=0)

                        with gr.Row():
                            cmp_a_btn = gr.Button("🔍 比對 A", variant="secondary", size="lg", scale=1, min_width=0)
                            cmp_b_btn = gr.Button("🔍 比對 B", variant="secondary", size="lg", scale=1, min_width=0)

                        with gr.Accordion("📋 元資料", open=True):
                            detail_meta = gr.Markdown(elem_classes="meta-panel")

                        with gr.Accordion("🤖 提示詞", open=False):
                            detail_prompt = gr.Markdown()

                        with gr.Accordion("📄 原始 JSON", open=False):
                            json_view_btn = gr.Button("載入 JSON", size="sm", variant="secondary")
                            json_display = gr.Code(language="json", lines=8)

                    # ── Right: Gallery ────────────
                    with gr.Column(scale=4, elem_classes="main-gallery"):
                        gallery = gr.Gallery(
                            label="媒體庫",
                            columns=4,
                            height=1050,
                            object_fit="cover",
                            allow_preview=True,
                            show_label=False,
                            elem_id="main-gallery",
                        )


            # ═══════════════════════════════════════
            # Tab 2: Favorites
            # ═══════════════════════════════════════
            with gr.Tab("⭐ 收藏"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=1, min_width=270, elem_classes="sidebar"):
                        fav_title_txt = gr.Markdown("#### 🔎 詳情")
                        fav_detail_image = gr.Image(show_label=False, height=300, interactive=False)
                        fav_detail_video = gr.Video(show_label=False, height=300, visible=False)
                        with gr.Row():
                            fav_fav_btn = gr.Button("💛 取消收藏", variant="secondary", size="sm")
                            fav_dl_btn = gr.DownloadButton("⬇️ 下載", variant="secondary", size="sm")
                        with gr.Accordion("📋 元資料", open=True):
                            fav_detail_meta = gr.Markdown(elem_classes="meta-panel")
                        with gr.Accordion("🤖 提示詞", open=False):
                            fav_detail_prompt = gr.Markdown()

                    with gr.Column(scale=4):
                        fav_gallery = gr.Gallery(
                            label="我的收藏",
                            columns=4,
                            height=820,
                            object_fit="cover",
                            allow_preview=True,
                            show_label=False,
                            elem_id="fav-gallery",
                        )

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

        # ── Events ──────────────────────────────────

        # Folder picker
        pick_btn.click(fn=do_pick_folder, outputs=[folder_input])

        # Gallery → detail outputs needed for refresh reset
        _do = [detail_meta, detail_prompt, fav_btn, detail_image, detail_video, title_txt]

        # Refresh
        _ri = [folder_input, filter_radio, search_box]
        _ro = [gallery, cmp_select_a, cmp_select_b, cmp_a_btn, cmp_b_btn] + _do
        refresh_btn.click(refresh_gallery, _ri, _ro)
        folder_input.submit(refresh_gallery, _ri, _ro)
        filter_radio.change(refresh_gallery, _ri, _ro)
        search_box.submit(refresh_gallery, _ri, _ro)

        # Delete file
        js_confirm_delete = """
        function(folder, filter_type, search) {
            if (!confirm("確定要在磁碟中永久刪除這個檔案嗎？\\n(注意：此操作無法還原)")) {
                return ["CANCEL", filter_type, search];
            }
            return [folder, filter_type, search];
        }
        """

        def on_delete_file(folder, filter_type, search):
            if folder == "CANCEL":
                return tuple(gr.update() for _ in range(len(_ro)))
            
            path = _state["selected_path"]
            if path and Path(path).exists():
                try:
                    Path(path).unlink()
                    _state["selected_path"] = ""
                except Exception as e:
                    print(f"Delete file failed: {e}")
            else:
                return tuple(gr.update() for _ in range(len(_ro)))
            
            return refresh_gallery(folder, filter_type, search)

        del_btn.click(
            on_delete_file,
            inputs=_ri,
            outputs=_ro,
            js=js_confirm_delete
        )

        # Gallery → detail
        gallery.select(on_gallery_select, outputs=_do)

        fav_btn.click(on_fav_toggle, outputs=[fav_btn, fav_gallery])
        json_view_btn.click(on_json_view, outputs=[json_display])

        # Compare shortcut buttons from Browse tab
        cmp_a_btn.click(lambda: on_set_cmp_target("🔍 比對 A"), outputs=[cmp_select_a, cmp_a_btn])
        cmp_b_btn.click(lambda: on_set_cmp_target("🔍 比對 B"), outputs=[cmp_select_b, cmp_b_btn])
        
        compareA.click(lambda: on_set_cmp_target("🔍 比對 A"), outputs=[cmp_select_a, cmp_a_btn])
        compareB.click(lambda: on_set_cmp_target("🔍 比對 B"), outputs=[cmp_select_b, cmp_b_btn])
        # Favorites tab
        _fo = [fav_detail_meta, fav_detail_prompt, fav_fav_btn, fav_detail_image, fav_detail_video, fav_title_txt]
        fav_gallery.select(on_fav_gallery_select, outputs=_fo)
        fav_fav_btn.click(on_fav_toggle, outputs=[fav_fav_btn, fav_gallery])
        fav_dl_btn.click(on_download, outputs=[fav_dl_btn])

        # Compare
        cmp_btn.click(on_compare, [cmp_select_a, cmp_select_b],
                      [cmp_slider, cmp_meta_a, cmp_meta_b])

        demo.load(lambda: _fav_gallery(), outputs=[fav_gallery])

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
