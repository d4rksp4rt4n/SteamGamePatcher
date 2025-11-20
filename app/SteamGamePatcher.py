# -*- mode: python ; coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from io import BytesIO
import requests
from PIL import Image, ImageTk, ImageDraw, ImageFont
import tempfile
import logging
import time
import threading
import queue
import shutil
import webbrowser
from collections import defaultdict
import uuid # For safe temp dirs if needed
import platform # For OS checks if needed
import zipfile # Built-in for ZIP
import gdown

APP_VERSION = '1.31-beta'

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller onefile"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = Path(__file__).parent.absolute()
    return Path(base_path) / relative_path
    
# Setup logging to file in data/
def setup_logging():
    log_dir = Path('data')
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / 'patcher.log'
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout) # Also to console
        ]
    )
    logging.info(f"Steam Game Patcher {APP_VERSION} started. Logs in: {log_file}")

def log(*args):
    """Legacy wrapper for backward compat; use logging directly now."""
    message = ' '.join(map(str, args))
    logging.debug(message)
    
def get_app_font(size=10, weight="normal"):
    roboto_path = resource_path("Roboto-Regular.ttf")
    if roboto_path.exists():
        try:
            font = tkfont.Font(family="Roboto", size=size, weight=weight)
            logging.info(f"FONT: Using bundled Roboto")
            return font
        except Exception as e:
            logging.warning(f"Failed to load bundled Roboto: {e}")

    # Restante do código igual (candidates, fallback etc.)
    candidates = ["Segoe UI", "Roboto", "Calibri", "Arial", "Helvetica", "sans-serif"]
    for family in candidates:
        try:
            font = tkfont.Font(family=family, size=size, weight=weight)
            font.actual()
            logging.info(f"FONT: Using system font → {family}")
            return font
        except:
            continue
    font = tkfont.Font(family="Arial", size=size, weight=weight)
    return font

def ensure_7z_exe():
    """Extract 7z.exe and 7z.dll alongside the app if not present."""
    # Get the directory where the EXE is running
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
        bundled_dir = sys._MEIPASS
    else:
        app_dir = Path(__file__).parent
        bundled_dir = app_dir
 
    seven_zip = app_dir / '7z.exe'
    seven_dll = app_dir / '7z.dll'
 
    if seven_zip.exists() and seven_dll.exists():
        logging.info("7z.exe and 7z.dll already available alongside app")
        return
 
    logging.info("Extracting 7z.exe and 7z.dll from bundle...")
    try:
        bundled_7z = Path(bundled_dir) / '7z.exe'
        if not bundled_7z.exists():
            raise FileNotFoundError("7z.exe not found in bundle")
        shutil.copy2(bundled_7z, seven_zip)
        logging.info(f"7z.exe extracted to {seven_zip}")
        
        bundled_7z_dll = Path(bundled_dir) / '7z.dll'
        if not bundled_7z_dll.exists():
            raise FileNotFoundError("7z.dll not found in bundle")
        shutil.copy2(bundled_7z_dll, seven_dll)
        logging.info(f"7z.dll extracted to {seven_dll}")
    except Exception as e:
        logging.error(f"Failed to extract 7z.exe or 7z.dll: {e}")
        messagebox.showwarning("Missing 7z files", "7z.exe or 7z.dll not found. Download from https://www.7-zip.org and place in the app folder.")
        sys.exit(1)

def get_steam_path():
    logging.info("Finding Steam...")
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Valve\Steam")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        winreg.CloseKey(key)
        logging.info(f"Steam: {path}")
        return Path(path)
    except:
        p = Path(os.getenv("ProgramFiles(x86)")) / "Steam"
        if p.exists():
            logging.info(f"Steam fallback: {p}")
            return p
    return None

def get_installed_games(steam_path):
    installed = {}
    vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
    libs = [steam_path / "steamapps"]
    if vdf_path.exists():
        try:
            import vdf
            data = vdf.load(open(vdf_path, "r", encoding="utf-8"))
            for val in data.get("libraryfolders", {}).values():
                p = Path(val.get("path") if isinstance(val, dict) else val)
                if p.is_dir():
                    libs.append(p / "steamapps")
        except:
            pass
    for lib in libs:
        common = lib / "common"
        if not common.is_dir():
            continue
        for acf in lib.glob("appmanifest_*.acf"):
            appid = acf.stem.split("_")[1]
            try:
                with open(acf, "r", encoding="utf-8") as f:
                    for line in f:
                        if '"installdir"' in line:
                            dir_name = line.split('"')[3]
                            full = common / dir_name
                            if full.is_dir():
                                installed[appid] = full
                                logging.info(f"Game: {appid} -> {full}")
                            break
            except:
                pass
    logging.info(f"Installed: {len(installed)}")
    return installed

def load_box_art(steam_path, appid):
    """Steam box art loader + fallback to no-box-art.png"""
    appid = str(appid)
    logging.debug(f"\n=== BOX ART SEARCH FOR APPID: {appid} ===")
    logging.debug(f"Steam path: {steam_path}")

    cache_dir = steam_path / "appcache" / "librarycache"
    userdata_dir = steam_path / "userdata"
    candidates = []
    custom_grid = []

    # 1. Modern flat files
    for ext in ["jpg", "jpeg", "png"]:
        p = cache_dir / f"{appid}_library_600x900.{ext}"
        if p.exists():
            candidates.append(p)
            logging.debug(f"FOUND flat 600x900: {p.name}")

    # 2. Legacy deep scan
    legacy_root = cache_dir / appid
    if legacy_root.exists() and legacy_root.is_dir():
        for root, dirs, files in os.walk(legacy_root):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png')):
                    filepath = Path(root) / file
                    name = file.lower()
                    if any(k in name for k in ["library_600x900", "capsule", "header", "hero"]):
                        candidates.append(filepath)
                        logging.debug(f"FOUND in subfolder: {filepath.relative_to(cache_dir)}")

    # 3. Custom grid (supports .jpg too!)
    if userdata_dir.exists():
        for user in userdata_dir.iterdir():
            if not user.is_dir(): continue
            grid_dir = user / "config" / "grid"
            if grid_dir.exists():
                for ext in ["p.png", "p.jpg", "p.jpeg"]:
                    grid_file = grid_dir / f"{appid}{ext}"
                    if grid_file.exists():
                        custom_grid.append(grid_file)
                        logging.debug(f"FOUND CUSTOM GRID: {grid_file.name}")
                        break

    all_images = candidates + custom_grid
    if all_images:
        # Prefer custom grid → then highest priority → then newest
        if custom_grid:
            best = max(custom_grid, key=lambda x: x.stat().st_mtime)
        else:
            priorities = [
                lambda x: "library_600x900" in x.name.lower(),
                lambda x: "capsule" in x.name.lower(),
                lambda x: "header" in x.name.lower(),
                lambda x: "hero" in x.name.lower() and "blur" not in x.name.lower(),
            ]
            best = None
            for cond in priorities:
                matches = [f for f in candidates if cond(f)]
                if matches:
                    best = max(matches, key=lambda x: x.stat().st_mtime)
                    break
            if not best:
                best = max(candidates, key=lambda x: x.stat().st_mtime)

        try:
            img = Image.open(best).convert("RGB")
            logging.debug(f"Loaded real box art: {best.name}")
        except Exception as e:
            logging.warning(f"Failed to load real box art {best}: {e}")
            img = None
    else:
        img = None
        logging.debug("NO BOX ART FOUND IN STEAM → using placeholder")

    # FALLBACK: use no-box-art.png from app directory
    if not img:
        placeholder_path = resource_path("no-box-art.png")
        if placeholder_path.exists():
            try:
                img = Image.open(placeholder_path).convert("RGB")
                logging.info("Using bundled no-box-art.png")
            except Exception as e:
                logging.error(f"Failed to load bundled no-box-art.png: {e}")
                img = None
        else:
            logging.warning("Bundled no-box-art.png not found!")

        # Final fallback: pure black with text
    if not img:
            img = Image.new("RGB", (200, 300), (28, 28, 38))
            draw = ImageDraw.Draw(img)
            font = None
            roboto_path = resource_path("Roboto-Regular.ttf")
            if roboto_path.exists():
                try:
                    font = ImageFont.truetype(str(roboto_path), 22)
                except:
                    pass
            if not font:
                font = ImageFont.load_default(size=20)
                text = "No Box Art"
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                position = ((200 - text_width) // 2, (300 - text_height) // 2)
                draw.text(position, text, fill=(180, 180, 180), font=font)

    # Resize & center
    img.thumbnail((200, 300), Image.Resampling.LANCZOS)
    bg = Image.new("RGB", (200, 300), (28, 28, 38))
    offset = ((200 - img.width) // 2, (300 - img.height) // 2)
    bg.paste(img, offset, img if img.mode == 'RGBA' else None)

    photo = ImageTk.PhotoImage(bg)
    logging.debug("BOX ART READY (real or placeholder)")
    logging.debug("=== END SEARCH ===\n")
    return photo
    
class PatchSelectionDialog(tk.Toplevel):
    def __init__(self, parent, display_files, file_entries):
        super().__init__(parent)
        self.title("Select Patches & View Instructions")
        self.geometry("700x600")

        # Get the real main App instance (not the dialog itself)
        main_app = parent.get_main_app() if hasattr(parent, "get_main_app") else parent
        main_app.center_window(self, 700, 600)
        self.main_app = main_app  # ← Critical: store correct reference

        self.result = None
        self.file_entries = file_entries

        tk.Label(self,
                 text="Select patches to apply\n.txt files = instructions (double-click/right-click to view)",
                 font=get_app_font(11, "bold")).pack(pady=12)

        frame = tk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        self.listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, font=get_app_font(10))
        scrollbar = tk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        for line in display_files:
            self.listbox.insert(tk.END, line)

        self.listbox.bind("<<ListboxSelect>>", self.on_selection_change)
        self.listbox.bind('<Double-Button-1>', self.view_selected_txt)
        self.listbox.bind('<Button-3>', self.view_selected_txt)

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=15)

        self.apply_btn = tk.Button(btn_frame, text="Apply Selected Patches",
                                   command=self.apply, bg="#b52f2f", fg="white",
                                   font=get_app_font(10, "bold"), state=tk.DISABLED)
        self.apply_btn.pack(side=tk.LEFT, padx=10)

        # Cancel button now properly resets UI
        tk.Button(btn_frame, text="Cancel", command=self.on_closing).pack(side=tk.LEFT, padx=10)

        # Crucial: reset UI when window is closed with X or Cancel
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.on_selection_change()

    def on_selection_change(self, event=None):
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            self.apply_btn.config(state=tk.DISABLED)
            return
        all_txt = all(
            self.file_entries[i]['name'].lower().endswith('.txt')
            for i in selected_indices
        )
        if all_txt:
            self.apply_btn.config(state=tk.DISABLED, text="No patches to apply (only instructions)")
        else:
            self.apply_btn.config(state=tk.NORMAL, text="Apply Selected Patches")

    def view_selected_txt(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.file_entries):
            return
        f = self.file_entries[idx]
        if f['name'].lower().endswith('.txt'):
            InstructionsDialog(self, f)  # self.main_app passed via parent
        else:
            messagebox.showinfo("Not a text file", "This is a binary patch.\nIt will be applied when you click 'Apply'.")

    def apply(self):
        indices = self.listbox.curselection()
        self.result = list(indices) if indices else None
        self.destroy()  # → on_closing() will reset UI

    def on_closing(self):
        try:
            if self.main_app and self.main_app.winfo_exists():
                self.main_app.reset_ui()
        except:
            pass
        self.destroy()
        
class InstructionsDialog(tk.Toplevel):
    def __init__(self, parent, file_data):
        super().__init__(parent)
        self.title(f"Instructions: {file_data['name']}")
        self.geometry("800x600")

        # parent is PatchSelectionDialog → get main_app from it
        main_app = parent.main_app if hasattr(parent, "main_app") else parent
        main_app.center_window(self, 800, 600)

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        header = tk.Label(self, text=file_data.get('path', file_data['name']),
                         font=get_app_font(12, "bold"), fg="#0066CC")
        header.pack(pady=10)

        text_frame = tk.Frame(self)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        text_widget = scrolledtext.ScrolledText(
            text_frame,
            wrap=tk.WORD,
            font=get_app_font(11),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white"
        )
        text_widget.pack(fill=tk.BOTH, expand=True)

        file_id = file_data['id']
        temp_txt = Path(tempfile.gettempdir()) / f"instruction_{uuid.uuid4().hex}.txt"
        try:
            import gdown
            gdown.download(id=file_id, output=str(temp_txt), quiet=True)
            with open(temp_txt, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            text_widget.insert(tk.END, content)
        except Exception as e:
            text_widget.insert(tk.END, f"Failed to load instructions:\n\n{e}\n\nFile ID: {file_id}")
        finally:
            if temp_txt.exists():
                try: temp_txt.unlink()
                except: pass

        text_widget.config(state=tk.DISABLED)
        tk.Button(self, text="Close", command=self.destroy, font=get_app_font(10)).pack(pady=10)

    def on_close(self):
        try:
            if self.master and self.master.winfo_exists():
                # Find main app and reset UI
                main_app = self.master
                while hasattr(main_app, "main_app"):
                    main_app = main_app.main_app
                if hasattr(main_app, "reset_ui"):
                    main_app.reset_ui()
        except:
            pass
        self.destroy()
        
class ChangesDialog(tk.Toplevel):
    def __init__(self, parent, grouped_changes):
        super().__init__(parent)
        self.title("Latest Patch Changes")
        self.geometry("600x500")
        parent.center_window(self, 600, 500)
        self.transient(parent)
        self.grab_set()
        text_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=70, height=25, font=get_app_font( 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        for game, details in grouped_changes.items():
            if game == "Miscellaneous":
                text_widget.insert(tk.END, f"{game}:\n")
            else:
                text_widget.insert(tk.END, f"{game}:\n")
            for detail in details:
                text_widget.insert(tk.END, f" - {detail}\n")
            text_widget.insert(tk.END, "\n")
        text_widget.config(state=tk.DISABLED)
        tk.Button(self, text="Close", command=self.destroy).pack(pady=10)

class AboutDialog(tk.Toplevel):
    def __init__(self, parent, version):
        super().__init__(parent)
        self.title("About Steam Game Patcher")
        self.geometry("400x200")
        parent.center_window(self, 400, 200)
        self.transient(parent)
        self.grab_set()
        about_text = f"Steam Game Patcher {APP_VERSION}\n\nDatabase Version: {version}"
        tk.Label(self, text=about_text, justify=tk.LEFT, font=get_app_font( 10)).pack(pady=20)
        tk.Button(self, text="Open GitHub", command=lambda: webbrowser.open("https://github.com/d4rksp4rt4n/SteamGamePatcher")).pack(pady=5)
        tk.Button(self, text="Close", command=self.destroy).pack(pady=10)

# Main App
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Steam Game Patcher {APP_VERSION}")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = 1000
        height = 800
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(900, 600)        # Prevent it from getting too small
        self.resizable(True, True)    # Allow resizing both ways
       
        # Add window icon (handles frozen/bundled apps)
        icon_path = resource_path('icon.ico')
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
                logging.info(f"Window icon loaded: {icon_path}")
            except Exception as e:
                logging.warning(f"Failed to load icon {icon_path}: {e}")
        else:
            logging.warning("icon.ico not found in resources")
       
        self.current_appid = None
        self.current_install_dir = None
        self.dev_var = tk.StringVar(value="")
        self.pub_var = tk.StringVar(value="")
        self.notes_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        # Menu bar
        menubar = tk.Menu(self)
        self.option_add('*tearOff', False)
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Latest Patch Changes...", command=lambda: ChangesDialog(self, self.grouped_changes))
        menubar.add_cascade(label="View", menu=view_menu)
        
        # New Tools menu with Clear Cache
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Clear Cache", command=self.clear_cache)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About...", command=lambda: AboutDialog(self, self.version))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)
        # Auto-download database
        DB_URL = "https://raw.githubusercontent.com/d4rksp4rt4n/SteamGamePatcher/refs/heads/main/database/data/patches_database.json"
        DB_PATH = Path('data/patches_database.json')
        def download_database():
            try:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                r = requests.get(DB_URL, timeout=15)
                r.raise_for_status()
                with open(DB_PATH, 'w', encoding='utf-8') as f:
                    f.write(r.text)
                logging.info("Database auto-updated from GitHub")
                return True
            except Exception as e:
                logging.error(f"Auto-update failed (using local): {e}")
                return False
        # Download if needed
        updated = download_database() if not DB_PATH.exists() or (time.time() - DB_PATH.stat().st_mtime > 3600) else False
        if not DB_PATH.exists():
            messagebox.showerror("No Database", "Download failed. Check internet.")
            sys.exit(1)
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            self.folder_db = json.load(f)
      
        # Check for metadata and set status
        metadata = self.folder_db.get('metadata', {})
        self.version = metadata.get('version', 'Unknown')
        recent_changes = metadata.get('recent_changes', [])
      
        db_status = "Updated" if updated else "Up to date"
        self.db_status = f"Database Version: {self.version} | Status: {db_status}"
      
        # Group recent changes by game
        self.grouped_changes = self.group_recent_changes(recent_changes)
      
        # Cache for downloaded archives
        app_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        self.cache_dir = app_dir / 'cache'
        self.cache_dir.mkdir(exist_ok=True)
        logging.info(f"Cache dir initialized: {self.cache_dir}")
      
        steam = get_steam_path()
        if not steam:
            messagebox.showerror("Error", "Steam not found")
            sys.exit(1)
        self.installed = get_installed_games(steam)
        self.steam_path = steam # For box art
        # Build matches from unified folder_db using appid
        self.matches = []
        self.by_id = {} # appid -> {"dev_name": , "game_name": , "data": game_data}
        for dev_name, dev_data in self.folder_db.get('developers', {}).items():
            for game_name, game_data in dev_data.get("games", {}).items():
                appid_raw = game_data.get("appid")
                if appid_raw:
                    appid = str(appid_raw).strip()
                    if appid in self.installed:
                        match_info = {
                            "dev_name": dev_name,
                            "game_name": game_name,
                            "data": game_data
                        }
                        self.matches.append(match_info)
                        self.by_id[appid] = match_info
                        logging.info(f"MATCH: {appid} -> {game_name} by {dev_name}")
        # Sort matches alphabetically by game name
        self.matches = sorted(self.matches, key=lambda x: x['game_name'].lower())
        logging.info(f"FOUND {len(self.matches)} matched games with patches")
        self.build_gui()
        if self.matches:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.on_select(None)
        self.progress_frame = None
        self.ui_queue = queue.Queue()
        self.after(100, self.process_ui_queue)
   
    def clear_cache(self):
        """Clear the cache directory."""
        if messagebox.askyesno("Clear Cache", "Delete all cached patches? (Frees space)"):
            try:
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(exist_ok=True)
                logging.info("Cache cleared")
                messagebox.showinfo("Done", "Cache cleared!")
            except Exception as e:
                logging.error(f"Failed to clear cache: {e}")
                messagebox.showerror("Error", f"Failed to clear cache: {e}")
   
    def group_recent_changes(self, changes):
        """Group recent changes by game name."""
        grouped = defaultdict(list)
        for change in changes:
            # Extract game name (assuming it starts with the game name)
            parts = change.split(" - ", 1)
            if len(parts) >= 2:
                game = parts[0]
                details = parts[1]
                grouped[game].append(details)
            else:
                # Fallback if format doesn't match
                grouped["Miscellaneous"].append(change)
        return dict(grouped) # Convert back to regular dict
   
    def clear_details(self):
        self.img_label.configure(image="", text="No Image")
        self.dev_label.config(text="")
        self.pub_label.config(text="")
        self.notes_label.config(text="")
        self.status_label.config(text="")
        self.open_folder_btn.config(state=tk.DISABLED)
        self.launch_btn.config(state=tk.DISABLED)
        self.current_appid = None
        self.current_install_dir = None
   
    def process_ui_queue(self):
        try:
            while True:
                msg, args = self.ui_queue.get_nowait()

                if msg == "update_progress":
                    progress_var, value = args
                    if value == -1:
                        # Pulsing / indeterminate
                        if self.progress_bar_widget['mode'] != 'indeterminate':
                            self.progress_bar_widget.configure(mode='indeterminate')
                            self.progress_bar_widget.start(10)
                    else:
                        if self.progress_bar_widget['mode'] != 'determinate':
                            self.progress_bar_widget.stop()
                            self.progress_bar_widget.configure(mode='determinate')
                        progress_var.set(value)

                elif msg == "update_status":
                    label, text = args
                    label.config(text=text)

                elif msg == "update_speed":
                    label, text = args
                    label.config(text=text)

                elif msg == "reset_ui":
                    self.reset_ui()

        except queue.Empty:
            pass

        self.after(50, self.process_ui_queue)
        
    def process_patch(self, files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label):
        """Thread worker for download/extract/smart apply."""
        try:
            # Find local 7z.exe
            script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
            local_7z = script_dir / '7z.exe'
            if not local_7z.exists():
                raise FileNotFoundError("7z.exe not found. Please download from https://www.7-zip.org/ and place in script directory.")
            
            no_window_flag = 0x08000000 if sys.platform == 'win32' else 0

            for idx in selected_indices:
                f = files[idx]
                
                # --- NEW DEBUGGING BLOCK ---
                file_id   = f['id']
                file_name = f['name']
                file_path = f.get('path', file_name)
                
                raw_size = f.get('size', 'Unknown')
                expected_bytes = self.parse_size_bytes(raw_size)
                
                logging.info(f"Processing file: {file_name}")
                logging.info(f"Size from DB: '{raw_size}' -> Parsed bytes: {expected_bytes}")
                # ---------------------------

                if file_name.lower().endswith('.txt'):
                    self.ui_queue.put(("update_status", (status_label, f"Instructions viewed: {file_name}")))
                    continue

                # 1. CACHE HANDLING
                cache_file = self.cache_dir / file_name
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                
                use_cache = False
                if cache_file.exists():
                    actual_size = os.path.getsize(cache_file)
                    small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                    tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                    
                    # Integrity test for cached file
                    test_cmd = [str(local_7z), 't', str(cache_file)]
                    logging.info(f"Testing cached file integrity: {cache_file}")
                    
                    # Run with hidden window
                    test_result = subprocess.run(test_cmd, capture_output=True, text=True, creationflags=no_window_flag)
                    
                    if test_result.returncode != 0:
                        logging.warning(f"Cached file failed integrity: {test_result.stderr}. Deleting and forcing redownload.")
                        cache_file.unlink()
                        use_cache = False
                    else:
                        if tolerance_check or small_file_check:
                            use_cache = True
                            logging.info(f"Using cached: {file_name} ({actual_size} bytes) - integrity OK")
                        else:
                            logging.warning(f"Cached file size mismatch despite integrity pass - forcing redownload.")
                            cache_file.unlink()
                            use_cache = False
                
                output = cache_file 
                
                # 2. DOWNLOAD
                if not use_cache:
                    retries = 0
                    max_retries = 3
                    while retries < max_retries:
                        logging.info(f"Downloading {file_path} (attempt {retries+1}/{max_retries})")
                        self.ui_queue.put(("update_status", (status_label, f"Downloading: {file_path}")))
                        self.ui_queue.put(("update_progress", (progress_var, -1))) 
                        
                        # Call the threaded gdown function
                        self.download_with_gdown(file_id, output, expected_bytes or 0, progress_var, status_label, speed_label)
                        actual_size = os.path.getsize(output)
                        
                        small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                        tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                        if tolerance_check or small_file_check:
                            logging.info(f"Download size verified: {file_path} {actual_size} bytes")
                            
                            # Integrity test post-download
                            test_cmd = [str(local_7z), 't', str(output)]
                            logging.info(f"Testing downloaded archive integrity: {output}")
                            
                            # Run with hidden window
                            test_result = subprocess.run(test_cmd, capture_output=True, text=True, creationflags=no_window_flag)
                            
                            if test_result.returncode != 0:
                                logging.error(f"Downloaded archive failed integrity test: {test_result.stderr}")
                                raise RuntimeError(f"Downloaded archive failed integrity (7z t): {test_result.stderr}. File kept in cache.")
                            logging.info(f"Download and integrity verified: {file_path}")
                            break
                        
                        retries += 1
                        logging.warning(f"Size mismatch: expected {expected_bytes or 'Unknown'}, got {actual_size}. Retrying...")
                        if output.exists():
                            output.unlink()
                            
                    if retries >= max_retries:
                        raise ValueError(f"Download size mismatch after {max_retries} retries for {file_path}.")
                else:
                    self.ui_queue.put(("update_status", (status_label, f"Using cached patch: {file_path}")))
                    self.ui_queue.put(("update_progress", (progress_var, 0)))
                
                # 3. EXTRACT
                self.ui_queue.put(("update_status", (status_label, f"Extracting: {file_path}")))
                temp_extract_dir = Path(tempfile.mkdtemp())
                logging.info(f"Extracting {output} to {temp_extract_dir}")
                try:
                    if output.suffix.lower() == ".exe":
                        # Handle self-extracting EXEs with hidden window flags
                        cmd = [str(output), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
                        result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True, creationflags=no_window_flag)
                        if result.returncode != 0:
                            cmd = [str(output), '/S']
                            result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True, creationflags=no_window_flag)
                        if result.returncode != 0:
                            result = subprocess.run([str(output)], cwd=str(temp_extract_dir), capture_output=True, text=True, creationflags=no_window_flag)
                        
                        if result.returncode != 0:
                            raise RuntimeError(f"Self-extracting EXE failed all modes: {result.stderr}")
                        
                        files_extracted = os.listdir(temp_extract_dir)
                        if not files_extracted:
                            logging.warning("No files extracted by EXE—may need manual run.")
                    else:
                        # Pass progress_var here for the real-time bar!
                        self.extract_archive(output, temp_extract_dir, progress_var)
                finally:
                    pass # Keep temp dir for apply
                
                # 4. APPLY
                self.ui_queue.put(("update_status", (status_label, f"Applying: {file_path}")))
                overwritten, added, skipped = self.smart_apply_patch(temp_extract_dir, install_dir, status_label)
                logging.info(f"Applied: {overwritten} overwritten, {added} added, {skipped} skipped")
                shutil.rmtree(temp_extract_dir)
                logging.info(f"Completed {file_path}")
            
            self.ui_queue.put(("update_status", (status_label, "SUCCESS")))
            self.after(100, lambda: messagebox.showinfo("SUCCESS", f"Patched:\n{game_name}\n\nApplied: {len(selected_indices)} files"))
        except Exception as e:
            error_msg = str(e)
            if "7z.exe not found" in error_msg:
                error_msg += "\n\nDownload 7-Zip from https://www.7-zip.org/ and place 7z.exe in the script directory."
            elif "integrity" in error_msg.lower():
                error_msg += "\n\nFile kept in cache for manual repair."
            
            self.ui_queue.put(("update_status", (status_label, "FAILED")))
            logging.error(f"PATCH FAILED: {error_msg}")
            self.after(100, lambda msg=error_msg: messagebox.showerror("PATCH FAILED", msg))
        finally:
            self.ui_queue.put(("reset_ui", None))
            
    def patch(self):
        selected = self.tree.selection()
        if not selected:
            return
        tags = self.tree.item(selected[0])["tags"]
        appid = str(tags[0])
        match = self.by_id.get(appid)
        if not match:
            messagebox.showerror("ERROR", "No data")
            return

        game_name = match["game_name"]
        install_dir = self.installed.get(appid)
        if not install_dir or not install_dir.exists():
            messagebox.showerror("ERROR", f"Game not found locally:\n{install_dir}")
            return

        files = match["data"].get("files", [])
        if not files:
            messagebox.showerror("ERROR", "No patch files found for this game.")
            return

        # Prepare list for dialog
        display_files = []
        for f in files:
            size_str = f.get('size', 'Unknown')
            file_path = f.get('path', f['name'])
            display_files.append(f"{file_path} ({size_str})")

        if not messagebox.askyesno("APPLY PATCH", f"Patch:\n{game_name}\n\nTo:\n{install_dir}\n\nContinue?"):
            return

        # UI: disable button + preparing
        self.patch_btn.config(state="disabled", text="PREPARING...")
        self.status.config(text="Loading patch options...", fg="orange")
        self.update_idletasks()

        # Show selection dialog
        dialog = PatchSelectionDialog(self, display_files, files)
        self.wait_window(dialog)
        selected_indices = dialog.result
        if not selected_indices:
            self.reset_ui()
            return

        # === PROGRESS UI ===
        self.progress_frame = tk.Frame(self)
        self.progress_frame.pack(fill=tk.X, padx=15, pady=8)

        progress_var = tk.DoubleVar()
        self.progress_bar_widget = ttk.Progressbar(
            self.progress_frame, variable=progress_var, maximum=100, mode='indeterminate'
        )
        self.progress_bar_widget.pack(fill=tk.X, pady=(0, 4))
        self.progress_bar_widget.start(10)

        status_label = tk.Label(self.progress_frame, text="Starting download...", font=get_app_font(10))
        status_label.pack(anchor="w")

        speed_label = tk.Label(self.progress_frame, text="", font=get_app_font(9), fg="#00ff88")
        speed_label.pack(anchor="w")

        # Update bottom status
        self.status.config(text="Downloading & applying patches...", fg="#3399ff")

        # Start background thread
        thread = threading.Thread(
            target=self.process_patch,
            args=(files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label),
            daemon=True
        )
        thread.start()
     
    def parse_size_bytes(self, size_str):
        """Robustly parse size strings like '1,024 MB', '1.5GB', '200kb', or raw bytes."""
        import re
        
        # Safety check for None or "Unknown"
        if not size_str or str(size_str).strip().lower() == 'unknown':
            return None
        
        # 1. Convert to string and remove commas (fixes "1,024 MB")
        s = str(size_str).strip().replace(',', '')
        
        # 2. Regex to find number and unit
        match = re.search(r"([\d\.]+)\s*([KMGTP]?B)", s, re.IGNORECASE)
        
        if match:
            value = float(match.group(1))
            unit = match.group(2).upper()
            
            multipliers = {
                'B': 1,
                'KB': 1024,
                'MB': 1024 * 1024,
                'GB': 1024 * 1024 * 1024,
                'TB': 1024 * 1024 * 1024 * 1024
            }
            return int(value * multipliers.get(unit, 1))
            
        # 3. Fallback: If it's just a raw number (e.g. "1048576")
        if s.isdigit():
            return int(s)
            
        return None

    def download_with_gdown(self, file_id, output_path, expected_bytes, progress_var, status_label, speed_label):
        """
        Google Drive downloader for gdown 5.x+ – progress via smart polling.
        Uses threading instead of subprocess to work correctly in --noconsole mode.
        """
        output_path = Path(output_path)
        self.ui_queue.put(("update_status", (status_label, f"Downloading: {output_path.name}")))

        start_time = time.time()
        initial_size = output_path.stat().st_size if output_path.exists() else 0
        last_size = initial_size
        no_growth_count = 0
        max_no_growth = 10 

        posix_path = output_path.as_posix()
        
        # Variable to capture errors from the thread
        thread_error = []

        # Define the download target
        def run_gdown():
            try:
                # We import locally just to be safe, though global import is fine
                import gdown
                gdown.download(id=file_id, output=posix_path, quiet=True, resume=True)
            except Exception as e:
                thread_error.append(e)

        # Start gdown in a separate thread (Thread B)
        # The current thread (Thread A - process_patch) will loop and monitor size
        download_thread = threading.Thread(target=run_gdown, daemon=True)
        download_thread.start()

        logging.debug(f"Started gdown thread for {output_path.name}")

        # Poll while the download thread is alive
        while download_thread.is_alive():
            if output_path.exists():
                current_size = output_path.stat().st_size
                
                # Check for progress (growth)
                if current_size > last_size:
                    last_size = current_size
                    no_growth_count = 0

                    # Update progress bar
                    if expected_bytes and expected_bytes > 0:
                        percent = min(100, (current_size / expected_bytes) * 100)
                        self.ui_queue.put(("update_progress", (progress_var, percent)))
                    else:
                        self.ui_queue.put(("update_progress", (progress_var, -1)))
                    
                    # Calculate and update download speed
                    elapsed = time.time() - start_time
                    if elapsed > 0.5:
                        speed_mb = (current_size - initial_size) / elapsed / (1024 * 1024)
                        self.ui_queue.put(("update_speed", (speed_label, f"{speed_mb:.2f} MB/s")))
                else:
                    # No growth in this poll
                    no_growth_count += 1
                    # Note: We don't break here immediately, we let the thread finish naturally
                    # unless it hangs indefinitely, but gdown usually handles timeouts.
            
            # Pause briefly
            time.sleep(0.2)

        # Check for errors caught in the thread
        if thread_error:
            logging.error(f"gdown thread failed: {thread_error[0]}")
            raise RuntimeError(f"Download failed: {thread_error[0]}")

        # Final checks after completion
        if not output_path.exists():
             raise ValueError("Download finished but file not found.")

        actual_size = output_path.stat().st_size
        if actual_size > initial_size:
            self.ui_queue.put(("update_progress", (progress_var, 100)))
            self.ui_queue.put(("update_speed", (speed_label, "Download complete")))
            self.ui_queue.put(("update_status", (status_label, f"Download Complete: {output_path.name}")))
            logging.info(f"Download completed: {actual_size} bytes")
            return actual_size
        else:
             # It finished, but size didn't change (already downloaded?)
             self.ui_queue.put(("update_progress", (progress_var, 100)))
             self.ui_queue.put(("update_status", (status_label, f"File already present: {output_path.name}")))
             return actual_size

    def extract_with_7z(self, archive_path, extract_dir, progress_var=None):
        """Extract using 7z.exe and parse progress percentage."""
        import re  # Ensure re is available
        script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        local_7z = script_dir / '7z.exe'
        if not local_7z.exists():
            raise FileNotFoundError("7z.exe not found.")

        if not extract_dir.is_dir():
            extract_dir.mkdir(parents=True, exist_ok=True)
            
        # Win11/Safety fix
        if extract_dir.suffix == '.exe':
            extract_dir = extract_dir.with_suffix('')
            extract_dir.mkdir(exist_ok=True)

        # -bsp1 enables progress output to stdout
        cmd = [str(local_7z), 'x', str(archive_path), f'-o{extract_dir}', '-y', '-bsp1']
        
        no_window_flag = 0x08000000 if sys.platform == 'win32' else 0

        # Use Popen to read output in real-time
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            creationflags=no_window_flag
        )

        # Read output chunk by chunk to catch "xx%" updates
        while True:
            # Read small chunks (7-zip uses backspaces, so line-reading hangs)
            chunk = process.stdout.read(64)
            
            if not chunk and process.poll() is not None:
                break
            
            if chunk:
                try:
                    text = chunk.decode('utf-8', errors='ignore')
                    # Find numbers followed by %
                    matches = re.findall(r'\b(\d+)%', text)
                    if matches and progress_var:
                        # Take the last percentage found in this chunk
                        percent = int(matches[-1])
                        self.ui_queue.put(("update_progress", (progress_var, percent)))
                except:
                    pass

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
            
        logging.info(f"Extracted with 7z: {archive_path}")

    def extract_archive(self, archive_path, extract_dir, progress_var=None):
        """Independent extraction: Pure Python for ZIP, 7z.exe for others, with progress."""
        
        logging.debug(f"DEBUG: Archive path: {archive_path}")
        if not extract_dir.is_dir():
            extract_dir.mkdir(parents=True, exist_ok=True)
        
        # Win11 Safety
        if extract_dir.suffix == '.exe':
            extract_dir = extract_dir.with_suffix('')
            extract_dir.mkdir(exist_ok=True)
        
        ext = archive_path.suffix.lower()
        
        try:
            if ext == '.zip':
                # Built-in pure Python for ZIP
                # ZIP extraction in Python is hard to measure without slowing it down,
                # so we set the bar to "indeterminate" (pulsing) for ZIPs.
                if progress_var:
                    self.ui_queue.put(("update_progress", (progress_var, -1)))
                
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(extract_dir)
                    logging.info(f"Extracted ZIP: {len(zf.namelist())} files")
            else:
                # Use 7z.exe for 7Z/RAR with the new progress bar logic
                self.extract_with_7z(archive_path, extract_dir, progress_var)
                
            # Verify extraction
            if not os.listdir(extract_dir):
                logging.warning("Extraction produced no files—check archive.")
            
        except Exception as e:
            logging.error(f"Extraction failed: {e}")
            raise

    def smart_apply_patch(self, extract_dir, install_dir, status_label):
        """Scan and match files from extracted patch to game dir, with hybrid overwrite/add logic."""
        game_files = defaultdict(list)
        for root, dirs, files in os.walk(install_dir):
            for file in files:
                game_files[file.lower()].append(os.path.join(root, file))
      
        overwritten = 0
        added = 0
        skipped = 0
        for root, _, files in os.walk(extract_dir):
            for file in files:
                src = os.path.join(root, file)
                relative = Path(src).relative_to(extract_dir)
                default_dst = install_dir / relative
                matches = game_files.get(file.lower(), [])
                if matches:
                    if len(matches) == 1:
                        dst = matches[0]
                        shutil.copy2(src, dst)
                        overwritten += 1
                        self.ui_queue.put(("update_status", (status_label, f"OVERWRITTEN: {file}")))
                    else:
                        skipped += 1
                        logging.warning(f"MULTIPLE MATCHES for {file}: {matches} - Skipping")
                        self.ui_queue.put(("update_status", (status_label, f"SKIPPED (multi-match): {file}")))
                else:
                    # Add new file, preserving relative structure from patch
                    default_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, default_dst)
                    added += 1
                    self.ui_queue.put(("update_status", (status_label, f"ADDED: {file}")))
        return overwritten, added, skipped
        
    def patch(self):
        selected = self.tree.selection()
        if not selected:
            return
        tags = self.tree.item(selected[0])["tags"]
        appid = str(tags[0])
        match = self.by_id.get(appid)
        if not match:
            messagebox.showerror("ERROR", "No data")
            return

        game_name = match["game_name"]
        install_dir = self.installed.get(appid)
        if not install_dir or not install_dir.exists():
            messagebox.showerror("ERROR", f"Game not found locally:\n{install_dir}")
            return

        files = match["data"].get("files", [])
        if not files:
            messagebox.showerror("ERROR", "No patch files found for this game.")
            return

        # Prepare list for dialog
        display_files = []
        for f in files:
            size_str = f.get('size', 'Unknown')
            file_path = f.get('path', f['name'])
            display_files.append(f"{file_path} ({size_str})")

        if not messagebox.askyesno("APPLY PATCH", f"Patch:\n{game_name}\n\nTo:\n{install_dir}\n\nContinue?"):
            return

        # UI: disable button + preparing
        self.patch_btn.config(state="disabled", text="PREPARING...")
        self.status.config(text="Loading patch options...", fg="orange")
        self.update_idletasks()

        # Show selection dialog
        dialog = PatchSelectionDialog(self, display_files, files)
        self.wait_window(dialog)
        selected_indices = dialog.result
        if not selected_indices:
            self.reset_ui()
            return

        # === PROGRESS UI ===
        self.progress_frame = tk.Frame(self)
        self.progress_frame.pack(fill=tk.X, padx=15, pady=8)

        progress_var = tk.DoubleVar()
        self.progress_bar_widget = ttk.Progressbar(
            self.progress_frame, variable=progress_var, maximum=100, mode='indeterminate'
        )
        self.progress_bar_widget.pack(fill=tk.X, pady=(0, 4))
        self.progress_bar_widget.start(10)

        status_label = tk.Label(self.progress_frame, text="Starting download...", font=get_app_font(10))
        status_label.pack(anchor="w")

        speed_label = tk.Label(self.progress_frame, text="", font=get_app_font(9), fg="#00ff88")
        speed_label.pack(anchor="w")

        # Update bottom status
        self.status.config(text="Downloading & applying patches...", fg="#3399ff")

        # Start background thread
        thread = threading.Thread(
            target=self.process_patch,
            args=(files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label),
            daemon=True
        )
        thread.start()

   
    def build_gui(self):
        # Main container
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # LEFT SIDE
        left_frame = tk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        # Box art
        self.img_label = tk.Label(left_frame, bg="#222", text="No Image", font=get_app_font(9))
        self.img_label.pack(pady=10)

        # Details area
        details_frame = tk.Frame(left_frame)
        details_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Helper to create bold label + value
        def add_row(label_text, var, value_color="#ffffff"):
            row = tk.Frame(details_frame)
            row.pack(anchor="w", padx=12, pady=2)
            tk.Label(row, text=label_text, font=get_app_font(10, "bold"), fg="black").pack(side=tk.LEFT)
            tk.Label(row, textvariable=var, font=get_app_font(10), fg=value_color,
                     anchor="w", justify="left", wraplength=140).pack(side=tk.LEFT, fill=tk.X)

        add_row("Developer:  ", self.dev_var, "black")
        add_row("Publisher:   ", self.pub_var, "black")
        add_row("Notes:        ", self.notes_var, "black")
        add_row("Status:       ", self.status_var, "#4CAF50")

        # BUTTONS (fixed position)
        buttons_frame = tk.Frame(left_frame)
        buttons_frame.pack(fill=tk.X, pady=(0, 8))

        self.patch_btn = tk.Button(buttons_frame, text="Patch Selected Game",
                                   command=self.patch,
                                   font=get_app_font(12, "bold"),
                                   bg="#b52f2f", fg="white", height=2, relief="flat", cursor="hand2")
        self.patch_btn.pack(fill=tk.X, padx=12, pady=(8, 6))

        self.open_folder_btn = tk.Button(buttons_frame, text="Open Game Folder",
                                         command=self.open_folder, state=tk.DISABLED,
                                         font=get_app_font(10), bg="#333333", fg="#cccccc")
        self.open_folder_btn.pack(fill=tk.X, padx=12, pady=4)

        self.launch_btn = tk.Button(buttons_frame, text="Launch Game",
                                    command=self.launch_game, state=tk.DISABLED,
                                    font=get_app_font(10), bg="#333333", fg="#cccccc")
        self.launch_btn.pack(fill=tk.X, padx=12, pady=(4, 8))

        # RIGHT SIDE - Game list (unchanged)
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        search_frame = tk.Frame(right_frame)
        search_frame.pack(fill=tk.X, padx=0, pady=(0, 8))
        tk.Label(search_frame, text="Search:", font=get_app_font(10)).pack(side=tk.LEFT, padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var, font=get_app_font(10))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.search_entry.bind('<KeyRelease>', self.filter_games)

        self.tree = ttk.Treeview(right_frame, columns=("Game",), show="headings", selectmode="browse")
        self.tree.heading("Game", text="Game")
        self.tree.column("Game", width=400, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style()
        style.configure("Treeview", font=get_app_font(10))
        style.configure("Treeview.Heading", font=get_app_font(10, "bold"))

        for match in self.matches:
            appid = str(match["data"]["appid"]).strip()
            self.tree.insert("", "end", values=(match["game_name"],), tags=(appid,))

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # Bottom status bar
        bottom_frame = tk.Frame(self, bg="#1e1e1e")
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
        self.status = tk.Label(bottom_frame, text=self.db_status, anchor="w",
                               font=get_app_font(10), bg="#1e1e1e", fg="#00ff88", padx=12)
        self.status.pack(fill=tk.X, side=tk.LEFT, expand=True)
   
    def filter_games(self, event=None):
        search_term = self.search_var.get().lower().strip()
        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        # Filter and insert matching games (sorted)
        filtered_matches = [m for m in self.matches if search_term in m['game_name'].lower()]
        for match in filtered_matches:
            appid = str(match["data"]["appid"]).strip()
            self.tree.insert("", "end",
                values=(match["game_name"],),
                tags=(appid,)
            )
        # Clear selection if no matches
        if not self.tree.get_children():
            self.clear_details()
   
    def on_select(self, _):
        selected = self.tree.selection()
        if not selected:
            self.clear_details()
            return
        tags = self.tree.item(selected[0])["tags"]
        if not tags:
            self.clear_details()
            return
        appid = str(tags[0])
        match = self.by_id.get(appid)
        if not match:
            self.clear_details()
            return

        # Load box art
        img = load_box_art(self.steam_path, appid)
        if img:
            self.img_label.configure(image=img, text="")
            self.img_label.image = img  # Keep reference!
        else:
            self.img_label.configure(image="", text="No box art")

        # CORRECT WAY: Update StringVars (this updates the labels automatically)
        self.dev_var.set(match['dev_name'])
        self.pub_var.set(match['data'].get('publisher', 'N/A'))
        self.notes_var.set(match['data'].get('notes', 'N/A'))
        self.status_var.set(match['data'].get('store_status', 'N/A'))

        # Enable buttons
        self.current_appid = appid
        self.current_install_dir = self.installed[appid]
        self.open_folder_btn.config(state=tk.NORMAL)
        self.launch_btn.config(state=tk.NORMAL)
   
    def open_folder(self):
        if self.current_install_dir and self.current_install_dir.exists():
            os.startfile(str(self.current_install_dir))
        else:
            messagebox.showerror("Error", "Game folder not found")
   
    def launch_game(self):
        if self.current_appid:
            url = f"steam://run/{self.current_appid}"
            os.startfile(url)
        else:
            messagebox.showerror("Error", "No game selected")
   
    def reset_ui(self):
        try:
            # Only touch widgets if they still exist
            if hasattr(self, 'patch_btn') and self.patch_btn.winfo_exists():
                self.patch_btn.config(state="normal", text="Patch Selected Game")
            if hasattr(self, 'status') and self.status.winfo_exists():
                self.status.config(text=self.db_status, fg="#00ff88")
            if hasattr(self, 'progress_frame') and self.progress_frame and self.progress_frame.winfo_exists():
                self.progress_frame.destroy()
                self.progress_frame = None
        except:
            pass  # App is closing or widgets gone → ignore silently
                
    def center_window(self, window, width=None, height=None):
        """Center any Toplevel window over the main app window"""
        window.update_idletasks()  # Ensure size is calculated
        main_x = self.winfo_rootx()
        main_y = self.winfo_rooty()
        main_w = self.winfo_width()
        main_h = self.winfo_height()

        win_w = width or window.winfo_width()
        win_h = height or window.winfo_height()

        x = main_x + (main_w - win_w) // 2
        y = main_y + (main_h - win_h) // 2

        # Keep window on screen (safety)
        x = max(0, x)
        y = max(0, y)

        window.geometry(f"{win_w}x{win_h}+{x}+{y}")
        
    def get_main_app(self):
        return self
   

if __name__ == "__main__":
    setup_logging() # Initialize logging to file and console
    ensure_7z_exe() # Run this first for standalone
    try:
        import vdf
    except:
        subprocess.call([sys.executable, "-m", "pip", "install", "vdf"])
        import vdf
    try:
        import gdown
    except:
        subprocess.call([sys.executable, "-m", "pip", "install", "gdown"])
        import gdown
    App().mainloop()
