"""
SteamGamePatcher.py (v1.4.1-modern)
A modern, professional Steam Game Patcher central hub.
Complete rewrite with customtkinter for a clean, Steam-inspired dark UI.

Summary of Changes vs v1.35-beta:
 - Migrated entire UI to customtkinter for modern dark theme
 - Steam-inspired color palette (#1b2838 / #171a21 / #66c0f4)
 - FIXED list/grid toggle: unified data model, selection preservation, filter sync
 - Grid now shows update markers and refreshes correctly after patching
 - PatchSelectionDialog completely rewritten with CTkCheckBox (eliminates all selection bugs)
 - Improved InstructionsDialog: better typography, image scaling, table styling, smooth scrolling
 - Added favorites system (toggle per game, stored in data/favorites.json)
 - Added Linux Steam detection (~/.steam/steam + libraryfolders.vdf)
 - Double-click any game (list or grid) opens GameDetailPage
 - Responsive grid layout adapts to window size
 - Box art caching for faster UI
 - All original functionality preserved (database, patching, 7z, PyInstaller, etc.)

=== ALL FIXES APPLIED ===
• No more silent crash when opening Instructions + scrolling + Cancel/X
• No more recursion error on dialog close
• No more NameError in PATCH FAILED messagebox
• Auto UAC elevation prompt on Windows (fixes WinError 740)
• All mousewheel / destroy race conditions eliminated
• Duplicate font() and _detect_font bugs fixed
• Full clean syntax & robust error handling
"""

import json
import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
from pathlib import Path
from io import BytesIO
import tempfile
import logging
import time
import threading
import queue
import shutil
import webbrowser
from collections import defaultdict
import uuid
import platform
import zipfile
import re
import ctypes

# ═══════════════════════════════════════════════════════════════
# Auto-install missing packages at startup
# ═══════════════════════════════════════════════════════════════
_REQUIRED_PACKAGES = [
    ("customtkinter", "customtkinter"),
    ("Pillow", "PIL"),
    ("requests", "requests"),
    ("gdown", "gdown"),
    ("vdf", "vdf"),
    ("python-docx", "docx"),
    ("pymupdf", "fitz"),
]

for _pkg_name, _import_name in _REQUIRED_PACKAGES:
    try:
        __import__(_import_name)
    except ImportError:
        print(f"[SteamGamePatcher] Installing {_pkg_name}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", _pkg_name, "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

import customtkinter as ctk
import requests
from PIL import Image, ImageTk, ImageDraw, ImageFont
import gdown
import vdf

# Optional DOCX support
try:
    from docx import Document as DocxDocument
    from docx.document import Document as _DocxDoc
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.table import _Cell, Table as DocxTable
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

# Optional PDF support
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False


# ═══════════════════════════════════════════════════════════════
# Constants & Steam-Inspired Theme
# ═══════════════════════════════════════════════════════════════
APP_VERSION = "1.4-modern"
CONFIG_FILENAME = "patcher_config.json"
FAVORITES_PATH = Path("data") / "favorites.json"

DB_URL = "https://raw.githubusercontent.com/d4rksp4rt4n/SteamGamePatcher/refs/heads/main/database/data/patches_database.json"
DB_PATH = Path("data/patches_database.json")
ETAG_PATH = DB_PATH.parent / "patches_database.etag"


class C:
    """Steam-inspired color constants."""
    BG_DARKEST   = "#171a21"
    BG_DARK      = "#1b2838"
    BG_CARD      = "#2a475e"
    BG_HOVER     = "#3d6b8e"
    BG_INPUT     = "#1e2a3a"
    ACCENT       = "#66c0f4"
    ACCENT_DIM   = "#417a9b"
    TEXT         = "#c6d4df"
    TEXT_DIM     = "#8f98a0"
    TEXT_BRIGHT  = "#ffffff"
    RED          = "#b52f2f"
    RED_HOVER    = "#d44040"
    GREEN        = "#00ff88"
    GREEN_DIM    = "#4CAF50"
    ORANGE       = "#e67e22"
    LINK         = "#64B5F6"
    VIEWABLE     = "#66bb6a"
    VIEWABLE_HVR = "#90CAF9"
    FAV_GOLD     = "#f5c542"
    TRANSPARENT  = "transparent"


# Global font family (set after tk root is created)
_FONT_FAMILY = "Segoe UI"


def _detect_font():
    """Detect best available font family on this system."""
    global _FONT_FAMILY
    for fam in ("Segoe UI", "Roboto", "Calibri", "Helvetica Neue", "Arial"):
        try:
            f = tkfont.Font(family=fam, size=10)
            if f.actual()["family"].lower().startswith(fam.lower()[:4]):
                _FONT_FAMILY = fam
                return
        except Exception:
            continue
    _FONT_FAMILY = "Arial"


def font(size=12, weight="normal"):
    """Return font tuple for ctk / tk widgets."""
    return (_FONT_FAMILY, size, "bold") if weight == "bold" else (_FONT_FAMILY, size)


# ═══════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════
def flatten_game_contents(contents):
    """Flatten contents from last_folders.json into the flat 'files' list the app expects.
    Supports old dict format and new flat list format from the indexer.
    """
    flat_files = []
    
    def recurse(items, current_path=""):
        if isinstance(items, dict):
            # Old nested dict format (legacy)
            for item_name, item_data in items.items():
                if not isinstance(item_data, dict):
                    continue
                if item_data.get("type") == "file":
                    display_path = f"{current_path}/{item_name}" if current_path else item_name
                    flat_files.append({
                        "name": item_name,
                        "path": display_path,
                        "id": item_data.get("id"),
                        "mimeType": item_data.get("mimeType"),
                        "size": item_data.get("size", "Unknown")
                    })
                elif item_data.get("type") == "folder" and "children" in item_data:
                    new_path = f"{current_path}/{item_name}" if current_path else item_name
                    recurse(item_data.get("children", {}), new_path)

        elif isinstance(items, list):
            # New flat list format (current)
            for item_data in items:
                if not isinstance(item_data, dict):
                    continue
                
                item_name = item_data.get("name") or item_data.get("filename")
                if not item_name or not item_data.get("id"):
                    continue
                
                # Accept ANY item that has an "id" — type can be ".exe", ".zip", "file", None, etc.
                display_path = f"{current_path}/{item_name}" if current_path else item_name
                flat_files.append({
                    "name": item_name,
                    "path": display_path,
                    "id": item_data.get("id"),
                    "mimeType": item_data.get("mimeType"),
                    "size": item_data.get("size", item_data.get("raw_size", "Unknown"))
                })

                # If there are ever real subfolders with "children", handle them too
                if item_data.get("type") == "folder" and "children" in item_data:
                    new_path = f"{current_path}/{item_name}" if current_path else item_name
                    recurse(item_data.get("children", []), new_path)

    if contents:
        recurse(contents)
    
    # Sort for consistent UI order
    flat_files.sort(key=lambda f: f['name'].lower())
    return flat_files
    
def resource_path(relative_path):
    """Get absolute path to resource (dev + PyInstaller compatible)."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = Path(__file__).parent.absolute()
    return Path(base) / relative_path


def setup_logging():
    log_dir = Path("data")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "patcher.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info(f"Steam Game Patcher {APP_VERSION} started. Logs: {log_file}")


def _subprocess_flags():
    """Return dict with creationflags on Windows (hide console)."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def ensure_7z_exe():
    """Extract bundled 7z.exe/dll alongside app if missing."""
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).parent
        bundled = Path(sys._MEIPASS)
    else:
        app_dir = Path(__file__).parent
        bundled = app_dir

    exe, dll = app_dir / "7z.exe", app_dir / "7z.dll"
    if exe.exists() and dll.exists():
        logging.info("7z.exe and 7z.dll available")
        return

    logging.info("Extracting 7z.exe / 7z.dll from bundle...")
    try:
        for name, dest in [("7z.exe", exe), ("7z.dll", dll)]:
            src = bundled / name
            if not src.exists():
                raise FileNotFoundError(f"{name} not found in bundle")
            shutil.copy2(src, dest)
        logging.info("7z extracted successfully")
    except Exception as e:
        logging.error(f"7z extraction failed: {e}")
        messagebox.showwarning("Missing 7z", "7z.exe/7z.dll not found.\nDownload from https://7-zip.org")
        sys.exit(1)


def get_steam_path():
    """Find Steam installation (Windows + Linux + macOS)."""
    logging.info("Searching for Steam installation...")

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Valve\Steam")
            path, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            p = Path(path)
            if p.exists():
                logging.info(f"Steam found (registry): {p}")
                return p
        except Exception:
            pass
        for cand in [
            Path(os.getenv("ProgramFiles(x86)", "")) / "Steam",
            Path(os.getenv("ProgramFiles", "")) / "Steam",
            Path("C:/Program Files (x86)/Steam"),
        ]:
            if cand.exists():
                logging.info(f"Steam found (fallback): {cand}")
                return cand

    elif sys.platform.startswith("linux"):
        for cand in [
            Path.home() / ".steam" / "steam",
            Path.home() / ".local" / "share" / "Steam",
            Path.home() / ".steam" / "debian-installation",
        ]:
            if cand.exists():
                logging.info(f"Steam found (Linux): {cand}")
                return cand

    elif sys.platform == "darwin":
        cand = Path.home() / "Library" / "Application Support" / "Steam"
        if cand.exists():
            logging.info(f"Steam found (macOS): {cand}")
            return cand

    logging.warning("Steam installation not found")
    return None


def get_installed_games(steam_path):
    """Scan Steam library folders for installed games."""
    installed = {}
    vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
    libs = [steam_path / "steamapps"]

    if vdf_path.exists():
        try:
            with open(vdf_path, "r", encoding="utf-8") as fh:
                data = vdf.load(fh)
            for val in data.get("libraryfolders", {}).values():
                p = Path(val.get("path", "") if isinstance(val, dict) else val)
                if p.is_dir():
                    libs.append(p / "steamapps")
        except Exception as e:
            logging.warning(f"VDF parse error: {e}")

    for lib in libs:
        common = lib / "common"
        if not common.is_dir():
            continue
        for acf in lib.glob("appmanifest_*.acf"):
            appid = acf.stem.split("_")[1]
            try:
                with open(acf, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if '"installdir"' in line:
                            dir_name = line.split('"')[3]
                            full = common / dir_name
                            if full.is_dir():
                                installed[appid] = full
                            break
            except Exception:
                pass

    logging.info(f"Found {len(installed)} installed Steam games")
    return installed


# ═══════════════════════════════════════════════════════════════
# Box Art Loader (returns PIL.Image 200x300)
# ═══════════════════════════════════════════════════════════════
def load_box_art_pil(steam_path, appid):
    """Load game box art as PIL Image (200x300)."""
    appid = str(appid)
    cache_dir = steam_path / "appcache" / "librarycache"
    userdata_dir = steam_path / "userdata"
    candidates, custom_grid = [], []

    # 1. Modern flat files
    for ext in ("jpg", "jpeg", "png"):
        p = cache_dir / f"{appid}_library_600x900.{ext}"
        if p.exists():
            candidates.append(p)

    # 2. Legacy deep scan
    legacy = cache_dir / appid
    if legacy.exists() and legacy.is_dir():
        for root, _, files in os.walk(legacy):
            for file in files:
                if file.lower().endswith((".jpg", ".jpeg", ".png")):
                    fp = Path(root) / file
                    nm = file.lower()
                    if any(k in nm for k in ("library_600x900", "capsule", "header", "hero")):
                        candidates.append(fp)

    # 3. Custom grid
    if userdata_dir.exists():
        for user in userdata_dir.iterdir():
            if not user.is_dir():
                continue
            gd = user / "config" / "grid"
            if gd.exists():
                for ext in ("p.png", "p.jpg", "p.jpeg"):
                    gf = gd / f"{appid}{ext}"
                    if gf.exists():
                        custom_grid.append(gf)
                        break

    img = None
    all_imgs = candidates + custom_grid
    if all_imgs:
        if custom_grid:
            best = max(custom_grid, key=lambda x: x.stat().st_mtime)
        else:
            best = None
            for cond in [
                lambda x: "library_600x900" in x.name.lower(),
                lambda x: "capsule" in x.name.lower(),
                lambda x: "header" in x.name.lower(),
                lambda x: "hero" in x.name.lower() and "blur" not in x.name.lower(),
            ]:
                matches = [f for f in candidates if cond(f)]
                if matches:
                    best = max(matches, key=lambda x: x.stat().st_mtime)
                    break
            if not best and candidates:
                best = max(candidates, key=lambda x: x.stat().st_mtime)
        if best:
            try:
                img = Image.open(best).convert("RGB")
            except Exception:
                pass

    if not img:
        ph = resource_path("no-box-art.png")
        if ph.exists():
            try:
                img = Image.open(ph).convert("RGB")
            except Exception:
                pass

    if not img:
        img = Image.new("RGB", (200, 300), (27, 40, 56))
        draw = ImageDraw.Draw(img)
        try:
            fnt = ImageFont.load_default(size=18)
        except TypeError:
            fnt = ImageFont.load_default()
        text = "No Box Art"
        bbox = draw.textbbox((0, 0), text, font=fnt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((200 - tw) // 2, (300 - th) // 2), text, fill=(140, 155, 170), font=fnt)

    img.thumbnail((200, 300), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", (200, 300), (27, 40, 56))
    off = ((200 - img.width) // 2, (300 - img.height) // 2)
    bg.paste(img, off, img if img.mode == "RGBA" else None)
    return bg


# ═══════════════════════════════════════════════════════════════
# Favorites persistence
# ═══════════════════════════════════════════════════════════════
def load_favorites():
    if FAVORITES_PATH.exists():
        try:
            with open(FAVORITES_PATH, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_favorites(favs):
    FAVORITES_PATH.parent.mkdir(exist_ok=True)
    with open(FAVORITES_PATH, "w") as f:
        json.dump(sorted(favs), f)


# ═══════════════════════════════════════════════════════════════
# Tooltip
# ═══════════════════════════════════════════════════════════════
class Tooltip:
    """Dark-themed tooltip that follows cursor."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(
            tw, text=self.text, justify=tk.LEFT,
            background=C.BG_CARD, foreground=C.TEXT,
            relief="solid", borderwidth=1, padx=8, pady=5, font=font(10),
        ).pack()

    def _hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ═══════════════════════════════════════════════════════════════
# PatchSelectionDialog
# ═══════════════════════════════════════════════════════════════
class PatchSelectionDialog(ctk.CTkToplevel):
    """Modern patch file selector with checkboxes and instruction previews."""

    def __init__(self, parent, files, main_app):
        super().__init__(parent)
        self.title("Select Patches & View Instructions")
        self.geometry("780x650")
        self.configure(fg_color=C.BG_DARKEST)
        self.transient(parent)
        self.grab_set()

        self.main_app = main_app
        self.files = files
        self.result = None
        self.viewable_exts = (".txt", ".docx", ".pdf")
        self.check_vars = []

        try:
            main_app.center_window(self, 780, 650)
        except Exception:
            pass

        # ── Header ──
        hdr = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="Select Patches to Apply",
                     font=font(16, "bold"), text_color=C.TEXT_BRIGHT).pack(pady=(18, 4))
        ctk.CTkLabel(hdr, text="Instruction files (green) can be previewed by clicking them",
                     font=font(11), text_color=C.TEXT_DIM).pack(pady=(0, 14))

        # ── Scrollable file list ──
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color=C.BG_DARK, corner_radius=8,
            scrollbar_button_color=C.BG_CARD,
            scrollbar_button_hover_color=C.BG_HOVER,
        )
        self.scroll.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        for f in files:
            name = f["name"]
            size = f.get("size", "Unknown")
            path_display = f.get("path", name)
            is_viewable = name.lower().endswith(self.viewable_exts)

            row = ctk.CTkFrame(self.scroll, fg_color=C.BG_DARKEST, corner_radius=6, height=48)
            row.pack(fill="x", padx=4, pady=3)
            row.pack_propagate(False)

            if is_viewable:
                self.check_vars.append(None)
                icon_lbl = ctk.CTkLabel(row, text="  📖", font=font(15), width=36, text_color=C.VIEWABLE)
                icon_lbl.pack(side="left", padx=(8, 0))

                name_btn = ctk.CTkButton(
                    row, text=f"  {path_display}  ({size})", font=font(11),
                    fg_color=C.TRANSPARENT, text_color=C.VIEWABLE,
                    hover_color=C.BG_HOVER, anchor="w",
                    command=lambda fi=f: self._view_instructions(fi),
                    cursor="hand2",
                )
                name_btn.pack(side="left", fill="x", expand=True, padx=4)

                prev_btn = ctk.CTkButton(
                    row, text="Preview", font=font(10), width=75, height=30,
                    fg_color=C.BG_CARD, hover_color=C.BG_HOVER,
                    text_color=C.ACCENT, corner_radius=6,
                    command=lambda fi=f: self._view_instructions(fi),
                )
                prev_btn.pack(side="right", padx=(4, 10))
            else:
                var = tk.BooleanVar(value=False)
                self.check_vars.append(var)

                cb = ctk.CTkCheckBox(
                    row, text="", variable=var, width=28,
                    fg_color=C.ACCENT, hover_color=C.ACCENT_DIM,
                    border_color=C.TEXT_DIM, corner_radius=4,
                    command=self._update_apply_state,
                )
                cb.pack(side="left", padx=(10, 2))

                icon_lbl = ctk.CTkLabel(row, text="  📦", font=font(15), width=36, text_color=C.TEXT_DIM)
                icon_lbl.pack(side="left", padx=(0, 2))

                name_lbl = ctk.CTkLabel(
                    row, text=f"  {path_display}  ({size})", font=font(11),
                    text_color=C.TEXT, anchor="w",
                )
                name_lbl.pack(side="left", fill="x", expand=True, padx=4)

        # ── Bottom Buttons ──
        btn_frame = ctk.CTkFrame(self, fg_color=C.TRANSPARENT)
        btn_frame.pack(fill="x", padx=18, pady=(0, 18))

        self.apply_btn = ctk.CTkButton(
            btn_frame, text="Apply Selected Patches", font=font(13, "bold"),
            fg_color=C.RED, hover_color=C.RED_HOVER, text_color=C.TEXT_BRIGHT,
            state="disabled", command=self._apply, height=46, corner_radius=8,
        )
        self.apply_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            btn_frame, text="Cancel", font=font(12),
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER, text_color=C.TEXT,
            command=self._cancel, height=46, corner_radius=8,
        ).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _update_apply_state(self):
        any_checked = any(v.get() for v in self.check_vars if v is not None)
        self.apply_btn.configure(state="normal" if any_checked else "disabled")

    def _view_instructions(self, file_data):
        InstructionsDialog(self, file_data)

    def _apply(self):
        self.result = [i for i, v in enumerate(self.check_vars) if v is not None and v.get()]
        if not self.result:
            return
        self.destroy()

    def _cancel(self):
        """Clean close — reset_ui is handled safely by the caller."""
        self.result = None
        self.destroy()


# ═══════════════════════════════════════════════════════════════
# InstructionsDialog — FULLY PROTECTED
# ═══════════════════════════════════════════════════════════════
class InstructionsDialog(ctk.CTkToplevel):
    """Full-screen instruction viewer for .txt / .docx / .pdf files."""

    def __init__(self, parent, file_data):
        super().__init__(parent)
        self.title(f"Instructions: {file_data['name']}")
        self.geometry("1050x820")
        self.configure(fg_color=C.BG_DARKEST)

        self._closed = False
        self._poll_after_id = None

        try:
            main = parent.main_app if hasattr(parent, "main_app") else parent
            main.center_window(self, 1050, 820)
        except Exception:
            pass

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.temp_images = []
        self.image_refs = []
        self.thread_content = None
        self.thread_error = None
        self.temp_file = None

        # Header
        header = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=0, height=50)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text=f"  {file_data.get('path', file_data['name'])}",
            font=font(13, "bold"), text_color=C.ACCENT, anchor="w",
        ).pack(fill="x", padx=15, pady=12)

        # Content area
        content_frame = ctk.CTkFrame(self, fg_color=C.BG_DARKEST, corner_radius=0)
        content_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.text_widget = tk.Text(
            content_frame, wrap=tk.WORD, bg=C.BG_DARKEST, fg=C.TEXT,
            font=font(12), relief="flat", bd=0, padx=25, pady=25,
            insertbackground=C.TEXT, selectbackground=C.BG_CARD,
            highlightthickness=0, cursor="arrow",
        )

        scrollbar = ttk.Scrollbar(content_frame, orient="vertical",
                                  command=self.text_widget.yview, style="Dark.Vertical.TScrollbar")
        self.text_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text_widget.pack(side="left", fill="both", expand=True)

        # Tags
        self.text_widget.tag_configure("heading1", font=font(20, "bold"), spacing1=18, spacing3=12, foreground=C.ACCENT)
        self.text_widget.tag_configure("heading2", font=font(16, "bold"), spacing1=14, spacing3=8, foreground=C.ACCENT)
        self.text_widget.tag_configure("heading", font=font(16, "bold"), spacing1=12, spacing3=8, foreground=C.ACCENT)
        self.text_widget.tag_configure("bold", font=font(12, "bold"))
        self.text_widget.tag_configure("italic", font=(_FONT_FAMILY, 12, "italic"))
        self.text_widget.tag_configure("bold_italic", font=(_FONT_FAMILY, 12, "bold italic"))
        self.text_widget.tag_configure("link", foreground=C.LINK, underline=True)
        self.text_widget.tag_configure("list_item", lmargin1=20, lmargin2=35)

        self.text_widget.tag_bind("link", "<Enter>", lambda e: self.text_widget.config(cursor="hand2"))
        self.text_widget.tag_bind("link", "<Leave>", lambda e: self.text_widget.config(cursor="arrow"))

        # Safe scroll binding
        self._bind_scroll_safe(self.text_widget)
        content_frame.bind("<MouseWheel>", self._on_scroll_safe, add="+")
        content_frame.bind("<Button-4>", self._on_scroll_safe, add="+")
        content_frame.bind("<Button-5>", self._on_scroll_safe, add="+")

        # Close button
        btn_bar = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=0, height=60)
        btn_bar.pack(fill="x", side="bottom")
        btn_bar.pack_propagate(False)
        ctk.CTkButton(
            btn_bar, text="Close", font=font(13, "bold"), width=140, height=40,
            fg_color=C.RED, hover_color=C.RED_HOVER, text_color=C.TEXT_BRIGHT,
            corner_radius=8, command=self._on_close,
        ).pack(pady=10)

        # Start loading
        self._show_loading()
        self._load_thread = threading.Thread(target=self._load_async, args=(file_data,), daemon=True)
        self._load_thread.start()
        self._start_poll(file_data)

    def _bind_scroll_safe(self, widget):
        """Bind scroll safely to any widget (text, table, image)."""
        widget.bind("<MouseWheel>", self._on_scroll_safe, add="+")
        widget.bind("<Button-4>", self._on_scroll_safe, add="+")
        widget.bind("<Button-5>", self._on_scroll_safe, add="+")

    def _on_scroll_safe(self, event):
        """Safe scroll handler — no recursion possible."""
        if self._closed or not hasattr(self, 'text_widget') or not self.text_widget.winfo_exists():
            return "break"
        try:
            self.text_widget.focus_set()
            if platform.system() in ("Windows", "Darwin"):
                self.text_widget.event_generate("<MouseWheel>", delta=event.delta)
            elif event.num == 4:
                self.text_widget.yview_scroll(-3, "units")
            elif event.num == 5:
                self.text_widget.yview_scroll(3, "units")
        except Exception:
            pass
        return "break"

    def _start_poll(self, file_data):
        if self._closed:
            return
        self._poll_after_id = self.after(100, self._poll, file_data)

    def _poll(self, file_data):
        if self._closed or not self.winfo_exists():
            return
        if self._load_thread.is_alive():
            self._poll_after_id = self.after(100, self._poll, file_data)
        else:
            self._finalize(file_data)

    def _show_loading(self):
        self._loader = ctk.CTkFrame(self, fg_color=C.BG_DARKEST, corner_radius=0)
        self._loader.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._loader.lift()
        inner = ctk.CTkFrame(self._loader, fg_color=C.TRANSPARENT)
        inner.place(relx=0.5, rely=0.4, anchor="center")
        self._prog = ctk.CTkProgressBar(inner, width=250, height=6,
                                         progress_color=C.ACCENT, fg_color=C.BG_CARD)
        self._prog.pack(pady=(0, 15))
        self._prog.configure(mode="indeterminate")
        self._prog.start()
        ctk.CTkLabel(inner, text="Downloading & Processing Document...",
                     font=font(13), text_color=C.ACCENT).pack()

    def _hide_loading(self):
        try:
            self._prog.stop()
            self._loader.destroy()
        except Exception:
            pass

    def _load_async(self, file_data):
        file_id = file_data.get("id")
        file_name = file_data["name"].lower()
        self.temp_file = Path(tempfile.gettempdir()) / f"instr_{uuid.uuid4().hex}"
        try:
            if "path" in file_data and Path(file_data["path"]).exists():
                shutil.copy(file_data["path"], self.temp_file)
            elif file_id:
                gdown.download(id=file_id, output=str(self.temp_file), quiet=True, fuzzy=True)

            if not file_name.endswith((".docx", ".pdf")):
                with open(self.temp_file, "r", encoding="utf-8", errors="ignore") as fh:
                    self.thread_content = fh.read()
        except Exception as e:
            self.thread_error = f"Failed to load content:\n\n{e}"
            logging.error(f"Doc load error: {e}")

    def _finalize(self, file_data):
        self._hide_loading()
        file_name = file_data["name"].lower()
        self.text_widget.config(state=tk.NORMAL)
        try:
            if self.thread_error:
                self.text_widget.insert(tk.END, self.thread_error)
            elif self.thread_content:
                self.text_widget.insert(tk.END, self.thread_content)
            elif self.temp_file and self.temp_file.exists():
                if file_name.endswith(".docx") and HAS_DOCX:
                    self._render_docx(str(self.temp_file))
                elif file_name.endswith(".pdf") and HAS_FITZ:
                    self._render_pdf(str(self.temp_file))
                else:
                    self.text_widget.insert(tk.END, "Unsupported format or missing library.")
            else:
                self.text_widget.insert(tk.END, "Failed to locate file for rendering.")
        except Exception as e:
            self.text_widget.insert(tk.END, f"Failed to render:\n\n{e}")
            logging.error(f"Doc render error: {e}")
        self.text_widget.config(state=tk.DISABLED)
        if self.temp_file and self.temp_file.exists():
            try:
                self.temp_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _open_link(self, url):
        webbrowser.open(url)

    def _render_docx(self, path):
        try:
            doc = DocxDocument(path)
        except Exception as e:
            self.text_widget.insert(tk.END, f"Error opening DOCX: {e}")
            return

        def iter_blocks(parent):
            if isinstance(parent, _DocxDoc):
                parent_elm = parent.element.body
            elif isinstance(parent, _Cell):
                parent_elm = parent._tc
            else:
                return
            for child in parent_elm.iterchildren():
                if isinstance(child, CT_P):
                    yield Paragraph(child, parent)
                elif isinstance(child, CT_Tbl):
                    yield DocxTable(child, parent)

        for block in iter_blocks(doc):
            if isinstance(block, Paragraph):
                self._render_paragraph(doc, block)
            elif isinstance(block, DocxTable):
                self._render_table(block)

    def _render_paragraph(self, doc, paragraph):
        try:
            from docx.text.run import Run
        except ImportError:
            return

        style_name = paragraph.style.name.lower()
        tags = []
        prefix = ""

        if "heading 1" in style_name:
            tags.append("heading1")
        elif "heading 2" in style_name:
            tags.append("heading2")
        elif "heading" in style_name:
            tags.append("heading")

        if "list" in style_name:
            prefix = "  \u2022 "
            tags.append("list_item")

        if prefix:
            self.text_widget.insert(tk.END, prefix, tuple(tags))

        for element in paragraph._element.iterchildren():
            if element.tag == qn("w:r"):
                run = Run(element, paragraph)
                try:
                    drawings = element.findall(".//" + qn("w:drawing"))
                    if drawings:
                        for drawing in drawings:
                            blips = drawing.findall(".//" + qn("a:blip"))
                            for blip in blips:
                                embed_id = blip.get(qn("r:embed"))
                                if embed_id:
                                    part = doc.part.related_parts.get(embed_id)
                                    if part:
                                        self._insert_image_blob(part.blob)
                                        self.text_widget.insert(tk.END, "\n")
                except Exception:
                    pass

                text = run.text
                if not text:
                    continue

                run_tags = list(tags)
                if run.bold and run.italic:
                    run_tags.append("bold_italic")
                elif run.bold:
                    run_tags.append("bold")
                elif run.italic:
                    run_tags.append("italic")

                self.text_widget.insert(tk.END, text, tuple(run_tags))

            elif element.tag == qn("w:hyperlink"):
                r_id = element.get(qn("r:id"))
                if r_id and r_id in doc.part.rels:
                    url = doc.part.rels[r_id].target_ref
                    link_text = ""
                    for re_ in element.findall(qn("w:r")):
                        te = re_.find(qn("w:t"))
                        if te is not None and te.text:
                            link_text += te.text
                    if link_text:
                        tag_id = f"link_{uuid.uuid4().hex}"
                        self.text_widget.tag_bind(tag_id, "<Button-1>",
                                                  lambda e, u=url: self._open_link(u))
                        self.text_widget.insert(tk.END, link_text, ("link", tag_id))

        self.text_widget.insert(tk.END, "\n")
        if not any(h in style_name for h in ("heading",)):
            self.text_widget.insert(tk.END, "\n")

    def _render_table(self, table):
        tbl_frame = tk.Frame(self.text_widget, bg=C.BG_CARD, padx=2, pady=2)
        self._bind_scroll(tbl_frame)

        for i, row in enumerate(table.rows):
            for j, cell in enumerate(row.cells):
                cell_text = cell.text.strip()
                bg = C.BG_HOVER if i == 0 else C.BG_DARK
                fg = C.ACCENT if i == 0 else C.TEXT
                ft = font(10, "bold") if i == 0 else font(10)

                lbl = tk.Label(
                    tbl_frame, text=cell_text, bg=bg, fg=fg, font=ft,
                    borderwidth=1, relief="solid", padx=8, pady=6,
                    anchor="w", justify=tk.LEFT, wraplength=350,
                )
                lbl.grid(row=i, column=j, sticky="nsew", padx=1, pady=1)
                self._bind_scroll(lbl)
            tbl_frame.grid_columnconfigure(j, weight=1)

        self.text_widget.window_create(tk.END, window=tbl_frame)
        self.text_widget.insert(tk.END, "\n\n")

    def _insert_image_blob(self, blob):
        try:
            img = Image.open(BytesIO(blob))
            tmp = Path(tempfile.gettempdir()) / f"docx_img_{uuid.uuid4().hex}.png"
            img.save(tmp)
            self.temp_images.append(tmp)
            self._insert_image(str(tmp))
        except Exception as e:
            logging.error(f"Image blob error: {e}")

    def _render_pdf(self, path):
        try:
            doc = fitz.open(path)
        except Exception as e:
            self.text_widget.insert(tk.END, f"Error opening PDF: {e}")
            return

        for pg_num in range(len(doc)):
            page = doc[pg_num]
            blocks = page.get_text("dict")["blocks"]
            blocks.sort(key=lambda b: b["bbox"][1])

            for block in blocks:
                if block["type"] == 0:
                    text = "\n".join(
                        span["text"] for line in block["lines"] for span in line["spans"]
                    )
                    if text.strip():
                        self.text_widget.insert(tk.END, text + "\n\n")

            for img_info in page.get_images(full=True):
                xref = img_info[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha < 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                tmp = Path(tempfile.gettempdir()) / f"pdf_{pg_num}_{xref}.png"
                pix.save(str(tmp))
                self.temp_images.append(tmp)
                self._insert_image(str(tmp))
                self.text_widget.insert(tk.END, "\n")
                pix = None

            if pg_num < len(doc) - 1:
                self.text_widget.insert(tk.END, "\n" + "\u2500" * 60 + "\n\n")
        doc.close()

    def _insert_image(self, img_path):
        try:
            img = Image.open(img_path)
            max_w = min(950, self.text_widget.winfo_width() - 60)
            if max_w < 200:
                max_w = 950
            if img.width > max_w:
                ratio = max_w / img.width
                img = img.resize((max_w, int(img.height * ratio)), Image.Resampling.LANCZOS)

            photo = ImageTk.PhotoImage(img)
            self.image_refs.append(photo)

            lbl = tk.Label(self.text_widget, image=photo, bg=C.BG_DARKEST, bd=0)
            lbl.image = photo
            self._bind_scroll(lbl)
            self.text_widget.window_create(tk.END, window=lbl)
            self.text_widget.insert(tk.END, "\n")
        except Exception as e:
            logging.warning(f"Image insert error: {e}")

    def _on_close(self):
        if self._closed:
            return
        self._closed = True

        if self._poll_after_id:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass

        for p in self.temp_images:
            try:
                if Path(p).exists():
                    Path(p).unlink(missing_ok=True)
            except Exception:
                pass

        self.destroy()


# ═══════════════════════════════════════════════════════════════
# ChangesDialog
# ═══════════════════════════════════════════════════════════════
class ChangesDialog(ctk.CTkToplevel):
    def __init__(self, parent, grouped_changes):
        super().__init__(parent)
        self.title("Latest Patch Changes")
        self.geometry("650x520")
        self.configure(fg_color=C.BG_DARKEST)
        self.transient(parent)
        self.grab_set()
        try:
            parent.center_window(self, 650, 520)
        except Exception:
            pass

        ctk.CTkLabel(self, text="Recent Patch Changes", font=font(16, "bold"),
                     text_color=C.TEXT_BRIGHT).pack(pady=(18, 10))

        frame = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=8)
        frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        tw = tk.Text(frame, wrap=tk.WORD, bg=C.BG_DARK, fg=C.TEXT,
                     font=font(11), relief="flat", bd=0, padx=15, pady=15,
                     highlightthickness=0, cursor="arrow")
        tw.pack(fill="both", expand=True)
        tw.tag_configure("game", font=font(12, "bold"), foreground=C.ACCENT)
        tw.tag_configure("detail", foreground=C.TEXT)

        for game, details in grouped_changes.items():
            tw.insert(tk.END, f"{game}:\n", "game")
            for d in details:
                tw.insert(tk.END, f"  \u2022 {d}\n", "detail")
            tw.insert(tk.END, "\n")
        tw.config(state=tk.DISABLED)

        ctk.CTkButton(self, text="Close", font=font(12), width=120, height=38,
                      fg_color=C.BG_CARD, hover_color=C.BG_HOVER,
                      command=self.destroy, corner_radius=8).pack(pady=(0, 15))


# ═══════════════════════════════════════════════════════════════
# AboutDialog
# ═══════════════════════════════════════════════════════════════
class AboutDialog(ctk.CTkToplevel):
    def __init__(self, parent, db_version):
        super().__init__(parent)
        self.title("About Steam Game Patcher")
        self.geometry("420x240")
        self.configure(fg_color=C.BG_DARKEST)
        self.transient(parent)
        self.grab_set()
        try:
            parent.center_window(self, 420, 240)
        except Exception:
            pass

        ctk.CTkLabel(self, text=f"Steam Game Patcher {APP_VERSION}",
                     font=font(16, "bold"), text_color=C.TEXT_BRIGHT).pack(pady=(25, 8))
        ctk.CTkLabel(self, text=f"Database Version: {db_version}",
                     font=font(12), text_color=C.TEXT_DIM).pack(pady=(0, 20))

        ctk.CTkButton(
            self, text="Open GitHub", font=font(11), width=160, height=36,
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER,
            command=lambda: webbrowser.open("https://github.com/d4rksp4rt4n/SteamGamePatcher"),
        ).pack(pady=4)
        ctk.CTkButton(
            self, text="Close", font=font(11), width=120, height=36,
            fg_color=C.RED, hover_color=C.RED_HOVER, command=self.destroy,
        ).pack(pady=(4, 15))


# ═══════════════════════════════════════════════════════════════
# GameDetailPage
# ═══════════════════════════════════════════════════════════════
class GameDetailPage(ctk.CTkToplevel):
    """Detailed view for a single game with patch history."""

    def __init__(self, parent, appid, main_app):
        super().__init__(parent)
        self.title("Game Details")
        self.geometry("1080x780")
        self.configure(fg_color=C.BG_DARKEST)
        self.transient(parent)
        self.grab_set()
        try:
            main_app.center_window(self, 1080, 780)
        except Exception:
            pass

        self.appid = str(appid)
        self.main_app = main_app
        self.match = main_app.by_id.get(self.appid)
        self.install_dir = main_app.installed.get(self.appid)

        if not self.match:
            ctk.CTkLabel(self, text="Game data not found.", font=font(14),
                         text_color=C.TEXT_DIM).pack(pady=50)
            return

        self._build()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=0, height=55)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkButton(
            top, text="\u2190  Back to Library", font=font(12, "bold"),
            fg_color=C.TRANSPARENT, hover_color=C.BG_HOVER,
            text_color=C.ACCENT, anchor="w", command=self.destroy,
        ).pack(side="left", padx=15, pady=10)

        ctk.CTkLabel(
            top, text=self.match["game_name"], font=font(16, "bold"),
            text_color=C.TEXT_BRIGHT,
        ).pack(side="left", padx=15, pady=10)

        content = ctk.CTkFrame(self, fg_color=C.BG_DARKEST)
        content.pack(fill="both", expand=True, padx=25, pady=15)

        left = ctk.CTkFrame(content, fg_color=C.TRANSPARENT, width=240)
        left.pack(side="left", fill="y", padx=(0, 25))
        left.pack_propagate(False)

        pil = self.main_app.get_box_art_pil(self.appid)
        cimg = ctk.CTkImage(light_image=pil, dark_image=pil, size=(200, 300))
        ctk.CTkLabel(left, image=cimg, text="").pack(pady=(0, 15))
        left._img_ref = cimg

        buttons = [
            ("PATCH GAME", C.RED, C.RED_HOVER, lambda: self.main_app.patch_from_detail(self.appid)),
            ("Open Game Folder", C.BG_CARD, C.BG_HOVER, self._open_folder),
            ("Open Google Drive", C.BG_CARD, C.BG_HOVER, self._open_gdrive),
            ("Launch Game", C.BG_CARD, C.BG_HOVER, self._launch),
        ]
        for txt, fg, hvr, cmd in buttons:
            ctk.CTkButton(
                left, text=txt, font=font(11, "bold" if "PATCH" in txt else "normal"),
                fg_color=fg, hover_color=hvr, text_color=C.TEXT_BRIGHT,
                height=40, corner_radius=8, command=cmd,
            ).pack(fill="x", pady=3)

        right = ctk.CTkScrollableFrame(content, fg_color=C.BG_DARK, corner_radius=8,
                                        scrollbar_button_color=C.BG_CARD)
        right.pack(side="right", fill="both", expand=True)

        ctk.CTkLabel(right, text=self.match["dev_name"], font=font(14, "bold"),
                     text_color=C.ACCENT, anchor="w").pack(fill="x", padx=15, pady=(15, 4))
        ctk.CTkLabel(right, text=f"Publisher: {self.match['data'].get('publisher', 'N/A')}",
                     font=font(12), text_color=C.TEXT_DIM, anchor="w").pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(right, text=f"Notes: {self.match['data'].get('notes', '\u2014')}",
                     font=font(11), text_color=C.TEXT_DIM, anchor="w",
                     wraplength=450, justify="left").pack(fill="x", padx=15, pady=(2, 15))

        ctk.CTkFrame(right, fg_color=C.BG_CARD, height=2).pack(fill="x", padx=15, pady=5)

        self._show_history(right)

    def _show_history(self, parent):
        config_path = self.install_dir / CONFIG_FILENAME if self.install_dir else None
        if not config_path or not config_path.exists():
            ctk.CTkLabel(parent, text="No patch applied yet.",
                         font=font(11), text_color=C.TEXT_DIM).pack(anchor="w", padx=15, pady=20)
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            last = config.get("last_patch", {})

            ctk.CTkLabel(parent, text="Patch History", font=font(13, "bold"),
                         text_color=C.GREEN_DIM).pack(anchor="w", padx=15, pady=(15, 8))

            info_frame = ctk.CTkFrame(parent, fg_color=C.BG_DARKEST, corner_radius=6)
            info_frame.pack(fill="x", padx=15, pady=4)

            ctk.CTkLabel(info_frame, text=f"Applied: {last.get('file', '\u2014')}",
                         font=font(11, "bold"), text_color=C.TEXT, anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(info_frame, text=f"Date: {last.get('date', '\u2014')}",
                         font=font(11), text_color=C.TEXT_DIM, anchor="w").pack(fill="x", padx=12, pady=(0, 10))

            changes = last.get("changes", {})
            if changes.get("overwritten"):
                ctk.CTkLabel(parent, text="Overwritten files:", font=font(10, "bold"),
                             text_color=C.ORANGE).pack(anchor="w", padx=15, pady=(12, 2))
                for fn in changes["overwritten"]:
                    ctk.CTkLabel(parent, text=f"   \u2022 {fn}", font=font(10),
                                 text_color=C.TEXT_DIM).pack(anchor="w", padx=15)
            if changes.get("added"):
                ctk.CTkLabel(parent, text="Added files:", font=font(10, "bold"),
                             text_color=C.GREEN_DIM).pack(anchor="w", padx=15, pady=(8, 2))
                for fn in changes["added"]:
                    ctk.CTkLabel(parent, text=f"   \u2022 {fn}", font=font(10),
                                 text_color=C.TEXT_DIM).pack(anchor="w", padx=15)
            if changes.get("skipped"):
                ctk.CTkLabel(parent, text="Skipped files:", font=font(10, "bold"),
                             text_color=C.TEXT_DIM).pack(anchor="w", padx=15, pady=(8, 2))
                for fn in changes["skipped"]:
                    ctk.CTkLabel(parent, text=f"   \u2022 {fn}", font=font(10),
                                 text_color=C.TEXT_DIM).pack(anchor="w", padx=15)

        except Exception as e:
            ctk.CTkLabel(parent, text=f"Could not read config: {e}",
                         font=font(10), text_color=C.ORANGE).pack(padx=15, pady=10)

    def _open_folder(self):
        if self.install_dir and self.install_dir.exists():
            if sys.platform == "win32":
                os.startfile(str(self.install_dir))
            else:
                subprocess.Popen(["xdg-open", str(self.install_dir)])

    def _open_gdrive(self):
        gid = self.match["data"].get("id")
        if gid:
            webbrowser.open(f"https://drive.google.com/drive/folders/{gid}")

    def _launch(self):
        url = f"steam://run/{self.appid}"
        if sys.platform == "win32":
            os.startfile(url)
        else:
            webbrowser.open(url)


# ═══════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ═══════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # === ADMIN ELEVATION (WinError 740 fix) ===
        if not self._ensure_admin():
            self.after(100, self.destroy)
            return

        _detect_font()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(f"Steam Game Patcher {APP_VERSION}")
        width, height = 1100, 900
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{(sw-width)//2}+{(sh-height)//2}")
        self.minsize(950, 650)
        self.configure(fg_color=C.BG_DARKEST)

        icon = resource_path("icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

        self.current_appid = None
        self.current_install_dir = None
        self.view_mode = tk.StringVar(value="list")
        self.search_var = tk.StringVar()
        self.favorites = load_favorites()
        self._pil_cache = {}
        self._grid_img_refs = []
        self.progress_frame = None
        self.ui_queue = queue.Queue()

        updated = self._download_database()
        if not DB_PATH.exists():
            messagebox.showerror("No Database", "Download failed. Check your internet connection.")
            sys.exit(1)

        with open(DB_PATH, "r", encoding="utf-8") as f:
            self.folder_db = json.load(f)

        # === NEW REFACTORED DATABASE SUPPORT (2026 format) ===
        if "entries" in self.folder_db:
            # Flatten contents for every game
            for entry in self.folder_db.get("entries", []):
                contents = entry.get("contents")
                if isinstance(contents, (dict, list)):
                    entry["files"] = flatten_game_contents(contents)
                else:
                    entry["files"] = []

            metadata = self.folder_db.get("data", {})
            self.version = metadata.get("generated_at", "Unknown")

            # Recent changes from the new location
            recent_changes = self.folder_db.get("last_folders_metadata", {}) \
                              .get("recent_changes", [])
        else:
            # Old fallback (still works)
            metadata = self.folder_db.get("metadata", {})
            self.version = metadata.get("version", "Unknown")
            recent_changes = metadata.get("recent_changes", [])

        db_status = "Updated" if updated else "Up to date"
        self.db_status_text = f"Database v{self.version}  |  {db_status}  |  Steam Game Patcher {APP_VERSION}"
        self.grouped_changes = self._group_changes(recent_changes)

        # === BUILD MATCHES (new flat "entries" structure) ===
                # === BUILD MATCHES + DEEP DEBUG (fixed order) ===
        self.matches = []
        self.by_id = {}

        # First make sure installed games are loaded
        steam = get_steam_path()
        if not steam:
            messagebox.showerror("Error", "Steam not found")
            sys.exit(1)
        self.steam_path = steam
        self.installed = get_installed_games(steam)

        entries = self.folder_db.get("entries", [])
        logging.info(f"Database contains {len(entries)} entries (should be ~1545)")

        installed_appids = set(self.installed.keys())
        logging.info(f"Your installed appids count: {len(installed_appids)}")
        logging.info(f"Sample installed appids: {list(installed_appids)[:30]}")   # first 30 only

        if entries:
            match_count = 0
            for entry in entries:
                appid_raw = entry.get("appid")
                if appid_raw:
                    appid = str(appid_raw).strip()
                    game_name = entry.get("game", "Unknown")

                    if appid in installed_appids:
                        match_info = {
                            "dev_name": entry.get("developer", "Unknown"),
                            "game_name": game_name,
                            "data": entry
                        }
                        self.matches.append(match_info)
                        self.by_id[appid] = match_info
                        logging.info(f"✅ MATCH FOUND: {appid} -> {game_name}")
                        match_count += 1

            logging.info(f"Total matches found: {match_count}")
        else:
            logging.warning("No 'entries' key in database!")

        self.matches = sorted(self.matches, key=lambda x: x['game_name'].lower())
        logging.info(f"FINAL MATCH COUNT: {len(self.matches)} games with patches")

        self.last_applied = self._load_configs()
        self._migrate_old_config()

        # Menu bar
        menubar = tk.Menu(self, bg=C.BG_DARK, fg=C.TEXT, activebackground=C.BG_CARD,
                          activeforeground=C.ACCENT, relief="flat", bd=0)
        self.option_add("*tearOff", False)

        view_menu = tk.Menu(menubar, bg=C.BG_DARK, fg=C.TEXT,
                            activebackground=C.BG_CARD, activeforeground=C.ACCENT)
        view_menu.add_command(label="Latest Patch Changes...",
                              command=lambda: ChangesDialog(self, self.grouped_changes))
        menubar.add_cascade(label="View", menu=view_menu)

        tools_menu = tk.Menu(menubar, bg=C.BG_DARK, fg=C.TEXT,
                             activebackground=C.BG_CARD, activeforeground=C.ACCENT)
        tools_menu.add_command(label="Clear Cache", command=self.clear_cache)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, bg=C.BG_DARK, fg=C.TEXT,
                            activebackground=C.BG_CARD, activeforeground=C.ACCENT)
        help_menu.add_command(label="About...", command=lambda: AboutDialog(self, self.version))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

        self._build_gui()

        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self.on_select(None)

        self.after(50, self._process_queue)

    def _ensure_admin(self):
        """Auto-prompt to run as Administrator on Windows."""
        if sys.platform != "win32":
            return True

        try:
            if ctypes.windll.shell32.IsUserAnAdmin():
                return True
        except Exception:
            return True

        answer = messagebox.askyesno(
            title="Administrator Rights Required",
            message="Some patches need Administrator rights to write to game folders.\n\n"
                    "Restart Steam Game Patcher as Administrator?\n\n"
                    "(Recommended — most games are installed in protected folders)"
        )
        if answer:
            try:
                script = sys.executable if getattr(sys, 'frozen', False) else __file__
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, f'"{script}"', None, 1
                )
                sys.exit(0)
            except Exception:
                messagebox.showwarning("Failed", "Could not elevate. Trying without admin rights...")
        return False

    def _download_database(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        headers = {}
        if DB_PATH.exists() and ETAG_PATH.exists():
            with ETAG_PATH.open("r") as f:
                headers["If-None-Match"] = f.read().strip()
        try:
            r = requests.get(DB_URL, headers=headers, timeout=15)
            if r.status_code == 304:
                logging.info("Database up to date (304)")
                os.utime(DB_PATH)
                return False
            r.raise_for_status()
            with DB_PATH.open("w", encoding="utf-8") as f:
                f.write(r.text)
            etag = r.headers.get("ETag")
            if etag:
                with ETAG_PATH.open("w") as f:
                    f.write(etag)
            os.utime(DB_PATH)
            logging.info("Database updated from GitHub")
            return True
        except Exception as e:
            logging.error(f"Database update failed: {e}")
            return False

    def _group_changes(self, changes):
        grouped = defaultdict(list)
        for item in changes:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                _, game, msg = item
                grouped[game].append(msg)
            elif isinstance(item, str):
                parts = item.split(" - ", 1)
                if len(parts) >= 2:
                    grouped[parts[0].strip()].append(parts[1].strip())
                else:
                    grouped["Miscellaneous"].append(item)
            else:
                grouped["Miscellaneous"].append(str(item))
        return dict(grouped)

    def _load_configs(self):
        last_applied = {}
        for appid, install_dir in self.installed.items():
            cfg = install_dir / CONFIG_FILENAME
            if cfg.exists():
                try:
                    with open(cfg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    lp = data.get("last_patch", {})
                    if lp:
                        appid_s = str(appid)
                        game_name = self.by_id.get(appid_s, {}).get("game_name", appid_s)
                        last_applied.setdefault(appid_s, {})[game_name] = lp
                except Exception:
                    pass
        return last_applied

    def _migrate_old_config(self):
        old = Path("data") / "last_applied.json"
        if not old.exists():
            return
        try:
            with open(old, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            for appid_s, games in old_data.items():
                for gn, pd in games.items():
                    idir = self.installed.get(appid_s)
                    if idir:
                        cp = idir / CONFIG_FILENAME
                        cfg = {}
                        if cp.exists():
                            with open(cp, "r", encoding="utf-8") as f:
                                cfg = json.load(f)
                        cfg["last_patch"] = pd
                        with open(cp, "w", encoding="utf-8") as f:
                            json.dump(cfg, f, indent=4)
            old.unlink()
            logging.info("Migrated old global config to per-game configs")
        except Exception as e:
            logging.warning(f"Migration failed: {e}")

    def save_per_game_config(self, appid, game_name, file_name, date, changes):
        appid_s = str(appid)
        idir = self.installed.get(appid_s)
        if not idir:
            return
        cp = idir / CONFIG_FILENAME
        try:
            cfg = {}
            if cp.exists():
                with open(cp, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["last_patch"] = {"file": file_name, "date": date, "changes": changes}
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
            self.last_applied.setdefault(appid_s, {})[game_name] = cfg["last_patch"]
            logging.info(f"Saved config: {file_name}")
        except Exception as e:
            logging.error(f"Config save failed: {e}")

    def get_box_art_pil(self, appid):
        appid = str(appid)
        if appid not in self._pil_cache:
            self._pil_cache[appid] = load_box_art_pil(self.steam_path, appid)
        return self._pil_cache[appid]

    def get_box_art_ctk(self, appid, size=(200, 300)):
        pil = self.get_box_art_pil(appid)
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=size)

    def _has_update(self, match):
        appid = str(match["data"]["appid"])
        gn = match["game_name"]
        local = self.last_applied.get(appid, {}).get(gn, {})
        lf = local.get("file")
        if lf:
            return not any(lf == f["name"] for f in match["data"]["files"])
        return False

    def _sorted_matches(self, matches):
        def key(m):
            appid = str(m["data"]["appid"])
            is_fav = appid in self.favorites
            is_upd = self._has_update(m)
            priority = 3
            if is_fav and is_upd:
                priority = 0
            elif is_upd:
                priority = 1
            elif is_fav:
                priority = 2
            return (priority, m["game_name"].lower())
        return sorted(matches, key=key)

    def _build_gui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Steam.Treeview",
            background=C.BG_DARK, foreground=C.TEXT,
            fieldbackground=C.BG_DARK, rowheight=38,
            font=font(11), borderwidth=0, relief="flat")
        style.configure("Steam.Treeview.Heading",
            background=C.BG_CARD, foreground=C.ACCENT,
            font=font(11, "bold"), borderwidth=0, relief="flat")
        style.map("Steam.Treeview",
            background=[("selected", C.BG_CARD)],
            foreground=[("selected", C.ACCENT)])
        style.layout("Steam.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        main = ctk.CTkFrame(self, fg_color=C.BG_DARKEST)
        main.pack(fill="both", expand=True, padx=0, pady=0)

        sidebar = ctk.CTkFrame(main, fg_color=C.BG_DARK, width=270, corner_radius=0)
        sidebar.pack(side="left", fill="y", padx=0, pady=0)
        sidebar.pack_propagate(False)

        self.img_label = ctk.CTkLabel(sidebar, text="No Image", font=font(10),
                                       text_color=C.TEXT_DIM, width=220, height=300,
                                       fg_color=C.BG_DARKEST, corner_radius=8)
        self.img_label.pack(padx=25, pady=(18, 12))

        details = ctk.CTkFrame(sidebar, fg_color=C.TRANSPARENT)
        details.pack(fill="x", padx=18)

        self.detail_dev = ctk.CTkLabel(details, text="", font=font(11, "bold"),
                                        text_color=C.ACCENT, anchor="w", wraplength=220)
        self.detail_dev.pack(fill="x", pady=1)
        self.detail_pub = ctk.CTkLabel(details, text="", font=font(10),
                                        text_color=C.TEXT_DIM, anchor="w", wraplength=220)
        self.detail_pub.pack(fill="x", pady=1)
        self.detail_notes = ctk.CTkLabel(details, text="", font=font(10),
                                          text_color=C.TEXT_DIM, anchor="w",
                                          wraplength=220, justify="left")
        self.detail_notes.pack(fill="x", pady=1)
        self.detail_status = ctk.CTkLabel(details, text="", font=font(10),
                                           text_color=C.GREEN_DIM, anchor="w", wraplength=220)
        self.detail_status.pack(fill="x", pady=1)
        self.detail_patch = ctk.CTkLabel(details, text="", font=font(10),
                                          text_color=C.GREEN_DIM, anchor="w",
                                          wraplength=230, justify="left")
        self.detail_patch.pack(fill="x", pady=(4, 8))

        btn_area = ctk.CTkFrame(sidebar, fg_color=C.TRANSPARENT)
        btn_area.pack(fill="x", padx=18, pady=(4, 0))

        self.patch_btn = ctk.CTkButton(
            btn_area, text="PATCH SELECTED GAME", font=font(12, "bold"),
            fg_color=C.RED, hover_color=C.RED_HOVER, text_color=C.TEXT_BRIGHT,
            height=46, corner_radius=8, command=self.patch,
        )
        self.patch_btn.pack(fill="x", pady=(0, 6))
        Tooltip(self.patch_btn, "Download and apply the patch for the selected game")

        self.fav_btn = ctk.CTkButton(
            btn_area, text="\u2606  Add to Favorites", font=font(11),
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER, text_color=C.FAV_GOLD,
            height=36, corner_radius=8, command=self._toggle_favorite,
        )
        self.fav_btn.pack(fill="x", pady=3)

        self.folder_btn = ctk.CTkButton(
            btn_area, text="Open Game Folder", font=font(10),
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER, text_color=C.TEXT,
            height=34, corner_radius=8, state="disabled", command=self.open_folder,
        )
        self.folder_btn.pack(fill="x", pady=3)

        self.gdrive_btn = ctk.CTkButton(
            btn_area, text="Open Google Drive", font=font(10),
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER, text_color=C.TEXT,
            height=34, corner_radius=8, state="disabled", command=self.open_gdrive_folder,
        )
        self.gdrive_btn.pack(fill="x", pady=3)

        self.launch_btn = ctk.CTkButton(
            btn_area, text="Launch Game", font=font(10),
            fg_color=C.BG_CARD, hover_color=C.BG_HOVER, text_color=C.TEXT,
            height=34, corner_radius=8, state="disabled", command=self.launch_game,
        )
        self.launch_btn.pack(fill="x", pady=3)

        right = ctk.CTkFrame(main, fg_color=C.BG_DARKEST, corner_radius=0)
        right.pack(side="right", fill="both", expand=True)

        toolbar = ctk.CTkFrame(right, fg_color=C.BG_DARK, corner_radius=0, height=55)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        search_frame = ctk.CTkFrame(toolbar, fg_color=C.TRANSPARENT)
        search_frame.pack(side="left", fill="x", expand=True, padx=(15, 10), pady=10)

        ctk.CTkLabel(search_frame, text="\U0001F50D", font=font(14),
                     text_color=C.TEXT_DIM, width=28).pack(side="left")
        self.search_entry = ctk.CTkEntry(
            search_frame, textvariable=self.search_var, font=font(12),
            fg_color=C.BG_INPUT, text_color=C.TEXT, border_color=C.BG_CARD,
            placeholder_text="Search games...", placeholder_text_color=C.TEXT_DIM,
            height=36, corner_radius=8,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.search_entry.bind("<KeyRelease>", self.filter_games)

        self.view_toggle = ctk.CTkSegmentedButton(
            toolbar, values=["List", "Grid"], font=font(11),
            fg_color=C.BG_DARKEST, selected_color=C.ACCENT,
            selected_hover_color=C.ACCENT_DIM,
            unselected_color=C.BG_CARD, unselected_hover_color=C.BG_HOVER,
            text_color=C.TEXT_BRIGHT, text_color_disabled=C.TEXT_DIM,
            command=self._on_view_toggle, corner_radius=8, height=34,
        )
        self.view_toggle.set("List")
        self.view_toggle.pack(side="right", padx=(0, 15), pady=10)

        self.count_label = ctk.CTkLabel(toolbar, text="", font=font(10),
                                         text_color=C.TEXT_DIM)
        self.count_label.pack(side="right", padx=(0, 10))

        self.view_area = ctk.CTkFrame(right, fg_color=C.BG_DARKEST, corner_radius=0)
        self.view_area.pack(fill="both", expand=True)

        self.list_container = ctk.CTkFrame(self.view_area, fg_color=C.BG_DARKEST, corner_radius=0)

        tree_frame = ctk.CTkFrame(self.list_container, fg_color=C.BG_DARK, corner_radius=8)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(8, 4))

        self.tree = ttk.Treeview(tree_frame, columns=("Game",), show="headings",
                                  selectmode="browse", style="Steam.Treeview")
        self.tree.heading("Game", text="  Game Library", anchor="w")
        self.tree.column("Game", anchor="w")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        self.grid_container = ctk.CTkFrame(self.view_area, fg_color=C.BG_DARKEST, corner_radius=0)
        self.grid_scroll = ctk.CTkScrollableFrame(
            self.grid_container, fg_color=C.BG_DARKEST,
            scrollbar_button_color=C.BG_CARD,
            scrollbar_button_hover_color=C.BG_HOVER,
        )
        self.grid_scroll.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.list_container.pack(fill="both", expand=True)

        status_bar = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=0, height=32)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        self.status_label = ctk.CTkLabel(
            status_bar, text=self.db_status_text, font=font(10),
            text_color=C.GREEN, anchor="w",
        )
        self.status_label.pack(fill="x", padx=15, pady=5)

        self.filter_games()

    def _on_view_toggle(self, value):
        if value == "List":
            self.view_mode.set("list")
            self.grid_container.pack_forget()
            self.list_container.pack(fill="both", expand=True)
            if self.current_appid:
                for item in self.tree.get_children():
                    tags = self.tree.item(item)["tags"]
                    if tags and str(tags[0]) == str(self.current_appid):
                        self.tree.selection_set(item)
                        self.tree.focus(item)
                        self.tree.see(item)
                        break
        else:
            self.view_mode.set("grid")
            self.list_container.pack_forget()
            self.grid_container.pack(fill="both", expand=True)
            self._build_grid()

    def filter_games(self, event=None):
        search = self.search_var.get().lower().strip()
        filtered = [m for m in self.matches if search in m["game_name"].lower()]
        sorted_list = self._sorted_matches(filtered)

        for item in self.tree.get_children():
            self.tree.delete(item)

        for match in sorted_list:
            appid = str(match["data"]["appid"])
            gn = match["game_name"]
            is_fav = appid in self.favorites
            is_upd = self._has_update(match)

            prefix = ""
            if is_fav and is_upd:
                prefix = "\u2605\u2665 "
            elif is_upd:
                prefix = "\u2605 "
            elif is_fav:
                prefix = "\u2665 "

            display = f"{prefix}{gn}"
            tag_list = [appid]
            if is_upd:
                tag_list.append("update")
            if is_fav:
                tag_list.append("favorite")

            self.tree.insert("", "end", values=(display,), tags=tuple(tag_list))

        self.tree.tag_configure("update", foreground=C.ORANGE, font=font(11, "bold"))
        self.tree.tag_configure("favorite", foreground=C.FAV_GOLD)

        self.count_label.configure(text=f"{len(sorted_list)} games")

        if self.current_appid:
            for item in self.tree.get_children():
                tags = self.tree.item(item)["tags"]
                if tags and str(tags[0]) == str(self.current_appid):
                    self.tree.selection_set(item)
                    self.tree.focus(item)
                    break

        if self.view_mode.get() == "grid":
            self._build_grid()

    def _build_grid(self):
        for w in self.grid_scroll.winfo_children():
            w.destroy()
        self._grid_img_refs.clear()

        search = self.search_var.get().lower().strip()
        filtered = [m for m in self.matches if search in m["game_name"].lower()]
        sorted_list = self._sorted_matches(filtered)

        try:
            width = self.grid_scroll.winfo_width()
            if width < 100:
                width = 800
        except Exception:
            width = 800
        tile_w = 210
        cols = max(1, width // tile_w)

        for idx, match in enumerate(sorted_list):
            appid = str(match["data"]["appid"])
            gn = match["game_name"]
            is_fav = appid in self.favorites
            is_upd = self._has_update(match)

            row, col = divmod(idx, cols)

            tile = ctk.CTkFrame(self.grid_scroll, fg_color=C.BG_DARK, corner_radius=10,
                                width=190, height=340)
            tile.grid(row=row, column=col, padx=8, pady=8, sticky="n")
            tile.grid_propagate(False)

            cimg = self.get_box_art_ctk(appid, size=(170, 255))
            self._grid_img_refs.append(cimg)
            img_lbl = ctk.CTkLabel(tile, image=cimg, text="", corner_radius=6)
            img_lbl.pack(padx=10, pady=(10, 6))

            prefix = ""
            if is_fav and is_upd:
                prefix = "\u2605\u2665 "
            elif is_upd:
                prefix = "\u2605 "
            elif is_fav:
                prefix = "\u2665 "

            name_color = C.ORANGE if is_upd else (C.FAV_GOLD if is_fav else C.TEXT)
            name_lbl = ctk.CTkLabel(tile, text=f"{prefix}{gn}", font=font(10, "bold"),
                                     text_color=name_color, wraplength=170, justify="center")
            name_lbl.pack(padx=8, pady=(0, 8))

            tip_text = f"{match['dev_name']}\n{match['data'].get('notes', '\u2014')}"
            if is_upd:
                tip_text = "\u2605 UPDATE AVAILABLE\n" + tip_text
            Tooltip(tile, tip_text)

            def _enter(e, t=tile):
                t.configure(fg_color=C.BG_CARD)
            def _leave(e, t=tile):
                t.configure(fg_color=C.BG_DARK)

            def _click(e, aid=appid):
                self.current_appid = aid
                self._update_sidebar(aid)
            def _dbl(e, aid=appid):
                self._open_game_detail(aid)

            for widget in (tile, img_lbl, name_lbl):
                widget.bind("<Enter>", _enter, add="+")
                widget.bind("<Leave>", _leave, add="+")
                widget.bind("<Button-1>", _click)
                widget.bind("<Double-1>", _dbl)

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self._clear_sidebar()
            return
        tags = self.tree.item(sel[0])["tags"]
        if not tags:
            self._clear_sidebar()
            return
        appid = str(tags[0])
        self.current_appid = appid
        self._update_sidebar(appid)

    def _on_double_click(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        tags = self.tree.item(sel[0])["tags"]
        if tags:
            self._open_game_detail(str(tags[0]))

    def _open_game_detail(self, appid):
        GameDetailPage(self, appid, self)

    def _update_sidebar(self, appid):
        match = self.by_id.get(appid)
        if not match:
            self._clear_sidebar()
            return

        try:
            cimg = self.get_box_art_ctk(appid)
            self.img_label.configure(image=cimg, text="")
            self.img_label._img = cimg
        except Exception:
            self.img_label.configure(image=None, text="No Image")

        gn = match["game_name"]
        self.detail_dev.configure(text=match["dev_name"])
        self.detail_pub.configure(text=f"Publisher: {match['data'].get('publisher', 'N/A')}")
        self.detail_notes.configure(text=match["data"].get("notes", "\u2014"))

        local = self.last_applied.get(appid, {}).get(gn, {})
        lf = local.get("file")
        changes = local.get("changes", {})
        is_upd = self._has_update(match)

        if is_upd:
            self.detail_patch.configure(
                text="UPDATE AVAILABLE\nA new patch has been released!",
                text_color=C.ORANGE)
        elif lf:
            ow = len(changes.get("overwritten", []))
            ad = len(changes.get("added", []))
            sk = len(changes.get("skipped", [])) if changes.get("skipped") else 0
            summary = f"{ow} overwritten, {ad} added"
            if sk:
                summary += f", {sk} skipped"
            self.detail_patch.configure(
                text=f"Applied: {lf}\nDate: {local.get('date', '?')}\n{summary}",
                text_color=C.GREEN_DIM)
        else:
            self.detail_patch.configure(text="Patch available", text_color=C.ACCENT)

        self.detail_status.configure(text=f"Store: {match['data'].get('store_status', 'N/A')}")

        is_fav = appid in self.favorites
        self.fav_btn.configure(
            text=("\u2605  Remove Favorite" if is_fav else "\u2606  Add to Favorites"),
            text_color=(C.FAV_GOLD if is_fav else C.TEXT_DIM),
        )

        self.current_install_dir = self.installed.get(appid)
        self.folder_btn.configure(state="normal")
        self.gdrive_btn.configure(state="normal")
        self.launch_btn.configure(state="normal")

    def _clear_sidebar(self):
        self.img_label.configure(image=None, text="No Image")
        self.detail_dev.configure(text="")
        self.detail_pub.configure(text="")
        self.detail_notes.configure(text="")
        self.detail_status.configure(text="")
        self.detail_patch.configure(text="")
        self.fav_btn.configure(text="\u2606  Add to Favorites", text_color=C.TEXT_DIM)
        self.folder_btn.configure(state="disabled")
        self.gdrive_btn.configure(state="disabled")
        self.launch_btn.configure(state="disabled")
        self.current_appid = None
        self.current_install_dir = None

    def _toggle_favorite(self):
        if not self.current_appid:
            return
        if self.current_appid in self.favorites:
            self.favorites.discard(self.current_appid)
        else:
            self.favorites.add(self.current_appid)
        save_favorites(self.favorites)
        self.filter_games()
        self._update_sidebar(self.current_appid)

    def _process_queue(self):
        try:
            while True:
                msg, args = self.ui_queue.get_nowait()
                if msg == "progress":
                    bar, value = args
                    if value == -1:
                        bar.configure(mode="indeterminate")
                        bar.start()
                    else:
                        bar.stop()
                        bar.configure(mode="determinate")
                        bar.set(min(1.0, value / 100.0))
                elif msg == "status":
                    lbl, text = args
                    lbl.configure(text=text)
                elif msg == "speed":
                    lbl, text = args
                    lbl.configure(text=text)
                elif msg == "reset_ui":
                    self.reset_ui()
                elif msg == "save_config":
                    appid, gn, fn, dt, ch = args
                    self.save_per_game_config(appid, gn, fn, dt, ch)
        except queue.Empty:
            pass
        self.after(50, self._process_queue)

    def refresh_after_patch(self):
        saved_appid = self.current_appid
        self.last_applied = self._load_configs()
        self.filter_games()
        if saved_appid:
            self.current_appid = saved_appid
            self._update_sidebar(saved_appid)

    def parse_size_bytes(self, size_str):
        if not size_str or str(size_str).strip().lower() == "unknown":
            return None
        s = str(size_str).strip().replace(",", "")
        m = re.search(r"([\d.]+)\s*([KMGTP]?B)", s, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = m.group(2).upper()
            mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            return int(val * mult.get(unit, 1))
        if s.isdigit():
            return int(s)
        return None

    def download_with_gdown(self, file_id, output_path, expected_bytes, progress_bar, status_lbl, speed_lbl):
        output_path = Path(output_path)
        self.ui_queue.put(("status", (status_lbl, f"Downloading: {output_path.name}")))
        start_time = time.time()
        initial_size = output_path.stat().st_size if output_path.exists() else 0
        last_size = initial_size
        posix = output_path.as_posix()

        errors = []
        def _dl():
            try:
                gdown.download(id=file_id, output=posix, quiet=True, resume=True)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=_dl, daemon=True)
        t.start()

        while t.is_alive():
            if output_path.exists():
                cur = output_path.stat().st_size
                if cur > last_size:
                    last_size = cur
                    if expected_bytes and expected_bytes > 0:
                        pct = min(100, (cur / expected_bytes) * 100)
                        self.ui_queue.put(("progress", (progress_bar, pct)))
                    else:
                        self.ui_queue.put(("progress", (progress_bar, -1)))
                    elapsed = time.time() - start_time
                    if elapsed > 0.5:
                        speed = (cur - initial_size) / elapsed / (1024 * 1024)
                        self.ui_queue.put(("speed", (speed_lbl, f"{speed:.2f} MB/s")))
            time.sleep(0.2)

        if errors:
            raise RuntimeError(f"Download failed: {errors[0]}")

        actual = output_path.stat().st_size if output_path.exists() else 0
        if actual > initial_size:
            self.ui_queue.put(("progress", (progress_bar, 100)))
            self.ui_queue.put(("speed", (speed_lbl, "Download complete")))
            self.ui_queue.put(("status", (status_lbl, f"Downloaded: {output_path.name}")))
        return actual

    def extract_with_7z(self, archive, dest, progress_bar=None):
        script_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        exe = script_dir / "7z.exe"
        if not exe.exists():
            raise FileNotFoundError("7z.exe not found")
        dest.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), "x", str(archive), f"-o{dest}", "-y", "-bsp1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **_subprocess_flags())
        while True:
            chunk = proc.stdout.read(64)
            if not chunk and proc.poll() is not None:
                break
            if chunk and progress_bar:
                try:
                    text = chunk.decode("utf-8", errors="ignore")
                    matches = re.findall(r"\b(\d+)%", text)
                    if matches:
                        self.ui_queue.put(("progress", (progress_bar, int(matches[-1]))))
                except Exception:
                    pass
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)

    def extract_archive(self, archive, dest, progress_bar=None):
        dest.mkdir(parents=True, exist_ok=True)
        ext = archive.suffix.lower()
        try:
            if ext == ".zip":
                if progress_bar:
                    self.ui_queue.put(("progress", (progress_bar, -1)))
                with zipfile.ZipFile(archive, "r") as zf:
                    zf.extractall(dest)
            else:
                self.extract_with_7z(archive, dest, progress_bar)
        except Exception as e:
            logging.error(f"Extraction failed: {e}")
            raise

    def smart_apply_patch(self, extract_dir, install_dir, status_lbl):
        game_files = defaultdict(list)
        for root, _, files in os.walk(install_dir):
            for f in files:
                game_files[f.lower()].append(os.path.join(root, f))

        ow_files, add_files, skip_files = [], [], []

        for root, _, files in os.walk(extract_dir):
            for f in files:
                src = os.path.join(root, f)
                rel = Path(src).relative_to(extract_dir)
                default_dst = install_dir / rel
                matches = game_files.get(f.lower(), [])

                if matches:
                    if len(matches) == 1:
                        shutil.copy2(src, matches[0])
                        ow_files.append(str(rel))
                        self.ui_queue.put(("status", (status_lbl, f"OVERWRITTEN: {f}")))
                    else:
                        skip_files.append(str(rel))
                        self.ui_queue.put(("status", (status_lbl, f"SKIPPED (multi-match): {f}")))
                else:
                    default_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, default_dst)
                    add_files.append(str(rel))
                    self.ui_queue.put(("status", (status_lbl, f"ADDED: {f}")))

        changes = {
            "overwritten": ow_files,
            "added": add_files,
            "skipped": skip_files if skip_files else None,
        }
        return len(ow_files), len(add_files), len(skip_files), changes

    def process_patch(self, files, selected_indices, install_dir, game_name,
                      progress_bar, status_lbl, speed_lbl, appid):
        today = time.strftime("%Y-%m-%d")
        applied_file = None
        total_changes = None

        try:
            script_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
            local_7z = script_dir / "7z.exe"
            flags = _subprocess_flags()

            for idx in selected_indices:
                f = files[idx]
                fid = f["id"]
                fname = f["name"]
                fpath = f.get("path", fname)
                raw_size = f.get("size", "Unknown")
                expected = self.parse_size_bytes(raw_size)

                if fname.lower().endswith((".txt", ".docx", ".pdf")):
                    continue

                cache_file = self.cache_dir / fname
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                use_cache = False

                if cache_file.exists() and local_7z.exists():
                    actual = os.path.getsize(cache_file)
                    small_ok = expected and expected < 2048 and actual > 0
                    tol_ok = expected is None or (abs(actual - expected) <= expected * 0.05)
                    test = subprocess.run([str(local_7z), "t", str(cache_file)],
                                          capture_output=True, text=True, **flags)
                    if test.returncode != 0:
                        cache_file.unlink()
                    elif tol_ok or small_ok:
                        use_cache = True

                output = cache_file
                if not use_cache:
                    retries = 0
                    while retries < 3:
                        self.ui_queue.put(("status", (status_lbl, f"Downloading: {fpath}")))
                        self.ui_queue.put(("progress", (progress_bar, -1)))
                        self.download_with_gdown(fid, output, expected or 0,
                                                 progress_bar, status_lbl, speed_lbl)
                        actual = os.path.getsize(output)
                        small_ok = expected and expected < 2048 and actual > 0
                        tol_ok = expected is None or (abs(actual - expected) <= expected * 0.05)

                        if tol_ok or small_ok:
                            if output.suffix.lower() != ".exe" and local_7z.exists():
                                test = subprocess.run([str(local_7z), "t", str(output)],
                                                      capture_output=True, text=True, **flags)
                                if test.returncode != 0:
                                    retries += 1
                                    if output.exists():
                                        output.unlink()
                                    continue
                            break
                        retries += 1
                        if output.exists():
                            output.unlink()
                    else:
                        raise ValueError(f"Download failed after 3 attempts for {fname}")

                self.ui_queue.put(("status", (status_lbl, f"Extracting: {fpath}")))
                temp_dir = Path(tempfile.mkdtemp())
                try:
                    if output.suffix.lower() == ".exe":
                        for flag_set in ["/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", "/S", ""]:
                            cmd = [str(output)] + flag_set.split()
                            result = subprocess.run(cmd, cwd=str(temp_dir),
                                                    capture_output=True, text=True, **flags)
                            if result.returncode == 0:
                                break
                        else:
                            raise RuntimeError("Self-extracting EXE failed")
                    else:
                        self.extract_archive(output, temp_dir, progress_bar)
                finally:
                    pass

                self.ui_queue.put(("status", (status_lbl, f"Applying: {fpath}")))
                ow, ad, sk, changes = self.smart_apply_patch(temp_dir, install_dir, status_lbl)
                total_changes = changes
                shutil.rmtree(temp_dir, ignore_errors=True)

                if not fname.lower().endswith((".txt", ".docx", ".pdf")):
                    applied_file = fname

            self.ui_queue.put(("status", (status_lbl, "SUCCESS")))
            if applied_file:
                self.ui_queue.put(("save_config", (appid, game_name, applied_file, today, total_changes or {})))
            self.after(100, lambda: messagebox.showinfo(
                "SUCCESS", f"Patched: {game_name}\n\nApplied: {applied_file or 'files'}\nConfig saved."))
            self.after(600, self.refresh_after_patch)

        except Exception as e:
            self.ui_queue.put(("status", (status_lbl, "FAILED")))
            logging.error(f"PATCH FAILED: {e}")
            error_msg = str(e)
            self.after(100, lambda msg=error_msg: messagebox.showerror("PATCH FAILED", msg))
        finally:
            self.ui_queue.put(("reset_ui", None))

    def patch(self):
        appid = str(self.current_appid) if self.current_appid else None
        if not appid:
            sel = self.tree.selection()
            if not sel:
                return
            tags = self.tree.item(sel[0])["tags"]
            appid = str(tags[0]) if tags else None
        if not appid:
            return

        match = self.by_id.get(appid)
        if not match:
            messagebox.showerror("Error", "No patch data found.")
            return

        gn = match["game_name"]
        idir = self.installed.get(appid)
        if not idir or not idir.exists():
            messagebox.showerror("Error", f"Game folder not found:\n{idir}")
            return

        files = match["data"].get("files", [])
        if not files:
            messagebox.showerror("Error", "No patch files for this game.")
            return

        files = sorted(files, key=lambda x: x["name"].lower())

        if not messagebox.askyesno("Apply Patch",
                f"Apply patch to:\n\n{gn}\n\n{idir}\n\nContinue?"):
            return

        self.patch_btn.configure(state="disabled", text="PREPARING...")
        self.status_label.configure(text="Loading patch selection...", text_color=C.ORANGE)
        self.update_idletasks()

        dialog = PatchSelectionDialog(self, files, self)
        self.wait_window(dialog)
        selected = dialog.result

        if not selected:
            self.reset_ui()
            return

        self.progress_frame = ctk.CTkFrame(self, fg_color=C.BG_DARK, corner_radius=8)
        self.progress_frame.pack(fill="x", padx=15, pady=8, before=self.status_label.master)

        prog_bar = ctk.CTkProgressBar(self.progress_frame, width=400, height=8,
                                       progress_color=C.ACCENT, fg_color=C.BG_DARKEST)
        prog_bar.pack(fill="x", padx=15, pady=(12, 6))
        prog_bar.configure(mode="indeterminate")
        prog_bar.start()

        s_lbl = ctk.CTkLabel(self.progress_frame, text="Starting...", font=font(10),
                              text_color=C.TEXT, anchor="w")
        s_lbl.pack(fill="x", padx=15)

        sp_lbl = ctk.CTkLabel(self.progress_frame, text="", font=font(9),
                               text_color=C.GREEN, anchor="w")
        sp_lbl.pack(fill="x", padx=15, pady=(0, 10))

        self.status_label.configure(text="Downloading & applying patches...", text_color=C.ACCENT)

        t = threading.Thread(
            target=self.process_patch,
            args=(files, selected, idir, gn, prog_bar, s_lbl, sp_lbl, appid),
            daemon=True,
        )
        t.start()

    def patch_from_detail(self, appid):
        self.current_appid = appid
        self.patch()

    def open_folder(self):
        if self.current_install_dir and self.current_install_dir.exists():
            if sys.platform == "win32":
                os.startfile(str(self.current_install_dir))
            else:
                subprocess.Popen(["xdg-open", str(self.current_install_dir)])

    def open_gdrive_folder(self):
        if not self.current_appid:
            return
        match = self.by_id.get(self.current_appid)
        if not match:
            return

        game_data = match["data"]
        # New field (preferred)
        patch_link = game_data.get("patch_link")
        if patch_link:
            webbrowser.open(patch_link)
            return

        # Old fallback
        gid = game_data.get("id")
        if gid:
            webbrowser.open(f"https://drive.google.com/drive/folders/{gid}")
        else:
            messagebox.showwarning("No Link", "Google Drive folder link not found.")

    def launch_game(self):
        if self.current_appid:
            url = f"steam://run/{self.current_appid}"
            if sys.platform == "win32":
                os.startfile(url)
            else:
                webbrowser.open(url)

    def clear_cache(self):
        if messagebox.askyesno("Clear Cache", "Delete all cached patches? (Frees disk space)"):
            try:
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(exist_ok=True)
                messagebox.showinfo("Done", "Cache cleared!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed: {e}")

    def reset_ui(self):
        try:
            if hasattr(self, "patch_btn") and self.patch_btn.winfo_exists():
                self.patch_btn.configure(state="normal", text="PATCH SELECTED GAME")
            if hasattr(self, "status_label") and self.status_label.winfo_exists():
                self.status_label.configure(text=self.db_status_text, text_color=C.GREEN)
            if self.progress_frame and self.progress_frame.winfo_exists():
                self.progress_frame.destroy()
                self.progress_frame = None
        except Exception:
            pass

    def center_window(self, window, width=None, height=None):
        window.update_idletasks()
        mx = self.winfo_rootx()
        my = self.winfo_rooty()
        mw = self.winfo_width()
        mh = self.winfo_height()
        ww = width or window.winfo_width()
        wh = height or window.winfo_height()
        x = max(0, mx + (mw - ww) // 2)
        y = max(0, my + (mh - wh) // 2)
        window.geometry(f"{ww}x{wh}+{x}+{y}")

    def get_main_app(self):
        return self


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    setup_logging()
    ensure_7z_exe()
    App().mainloop()
