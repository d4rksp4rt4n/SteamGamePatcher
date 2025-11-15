# -*- mode: python ; coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from io import BytesIO
import requests
from PIL import Image, ImageTk
import tempfile
import logging
import time
import threading
import queue
import shutil
import webbrowser
from collections import defaultdict
import uuid  # For safe temp dirs if needed
import platform  # For OS checks if needed

APP_VERSION = '1.23beta'

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
            logging.StreamHandler(sys.stdout)  # Also to console
        ]
    )
    logging.info(f"Steam Game Patcher {APP_VERSION} started. Logs in: {log_file}")

def log(*args):
    """Legacy wrapper for backward compat; use logging directly now."""
    message = ' '.join(map(str, args))
    logging.debug(message)

def ensure_7z_exe():
    """Extract 7z.exe alongside the app if not present."""
    # Get the directory where the EXE is running
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
        bundled_dir = sys._MEIPASS
    else:
        app_dir = Path(__file__).parent
        bundled_dir = app_dir
  
    seven_zip = app_dir / '7z.exe'
  
    if seven_zip.exists():
        logging.info("7z.exe already available alongside app")
        return
  
    logging.info("Extracting 7z.exe from bundle...")
    try:
        bundled_7z = Path(bundled_dir) / '7z.exe'
        if not bundled_7z.exists():
            raise FileNotFoundError("7z.exe not found in bundle")
        shutil.copy2(bundled_7z, seven_zip)
        logging.info(f"7z.exe extracted to {seven_zip}")
    except Exception as e:
        logging.error(f"Failed to extract 7z.exe: {e}")
        messagebox.showwarning("Missing 7z.exe", "7z.exe not found. Download from https://www.7-zip.org and place in the app folder.")
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
    """ULTIMATE 2025 Steam box art loader — works with deleted games, custom .jpg grid, deep hash folders"""
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
    # 2. Legacy: deep scan ALL subfolders (handles hash-in-hash)
    legacy_root = cache_dir / appid
    if legacy_root.exists() and legacy_root.is_dir():
        logging.debug(f"Scanning legacy root: {legacy_root}")
        for root, dirs, files in os.walk(legacy_root):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png')):
                    filepath = Path(root) / file
                    name = file.lower()
                    if any(k in name for k in ["library_600x900", "capsule", "header", "hero"]):
                        candidates.append(filepath)
                        logging.debug(f"FOUND in subfolder: {filepath.relative_to(cache_dir)}")
    # 3. CUSTOM GRID — NOW SUPPORTS .JPG TOO!
    if userdata_dir.exists():
        for user in userdata_dir.iterdir():
            if not user.is_dir(): continue
            grid_dir = user / "config" / "grid"
            if grid_dir.exists():
                for ext in ["p.png", "p.jpg", "p.jpeg"]:
                    grid_file = grid_dir / f"{appid}{ext}"
                    if grid_file.exists():
                        custom_grid.append(grid_file)
                        logging.debug(f"FOUND CUSTOM GRID: {grid_file.name} (user: {user.name})")
                        break
    # === PICK BEST ===
    all_images = candidates + custom_grid
    if not all_images:
        logging.debug("NO BOX ART FOUND ANYWHERE")
        logging.debug("=== END SEARCH ===\n")
        return None
    # CUSTOM GRID ALWAYS WINS
    if custom_grid:
        best = max(custom_grid, key=lambda x: x.stat().st_mtime)
        logging.debug(f"WINNER: CUSTOM GRID -> {best.name}")
    else:
        # Priority: 600x900 > capsule > header > hero
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
                logging.debug(f"WINNER: PRIORITY -> {best.name}")
                break
        if not best:
            best = max(candidates, key=lambda x: x.stat().st_mtime)
            logging.debug(f"FALLBACK -> {best.name}")
    # Load
    try:
        img = Image.open(best).convert("RGB")
        logging.debug(f"Loaded: {img.size} -> {best.name}")
        img.thumbnail((200, 300), Image.Resampling.LANCZOS)
        bg = Image.new("RGB", (200, 300), (28, 28, 38))
        offset = ((200 - img.width) // 2, (300 - img.height) // 2)
        bg.paste(img, offset)
        photo = ImageTk.PhotoImage(bg)
        logging.debug("BOX ART LOADED PERFECTLY")
        logging.debug("=== END SEARCH ===\n")
        return photo
    except Exception as e:
        logging.error(f"FAILED: {e}")
        return None

class PatchSelectionDialog(tk.Toplevel):
    def __init__(self, parent, files):
        super().__init__(parent)
        self.title("Select Patches to Apply")
        self.geometry("500x400")
        self.result = None
        tk.Label(self, text="Select patches to apply:\n(Size shown for reference)").pack(pady=10)
        # Listbox with scroll
        frame = tk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, height=12)
        scrollbar = tk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for f in files:
            self.listbox.insert(tk.END, f)
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Apply Selected", command=self.apply).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=5)
        self.listbox.bind('<Double-Button-1>', self.apply)
    def apply(self, event=None):
        indices = self.listbox.curselection()
        if not indices:
            messagebox.showwarning("No Selection", "Select at least one patch.")
            return
        self.result = indices
        self.destroy()

class ChangesDialog(tk.Toplevel):
    def __init__(self, parent, grouped_changes):
        super().__init__(parent)
        self.title("Latest Patch Changes")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()
        text_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=70, height=25, font=("Arial", 10))
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
        self.transient(parent)
        self.grab_set()
        about_text = f"Steam Game Patcher {APP_VERSION}\n\nDatabase Version: {version}"
        tk.Label(self, text=about_text, justify=tk.LEFT, font=("Arial", 10)).pack(pady=20)
        tk.Button(self, text="Open GitHub", command=lambda: webbrowser.open("https://github.com/d4rksp4rt4n/SteamGamePatcher")).pack(pady=5)
        tk.Button(self, text="Close", command=self.destroy).pack(pady=10)

# Main App
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Steam Game Patcher {APP_VERSION}")
        self.geometry("960x640")
       
        # Add window icon (handles frozen/bundled apps)
        icon_path = self.get_resource_path('icon.ico')
        if icon_path and os.path.exists(icon_path):
            self.iconbitmap(icon_path) # For ICO files (Windows/macOS/Linux)
        else:
            logging.warning("Icon file not found; using default")
       
        self.current_appid = None
        self.current_install_dir = None
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
    
    def get_resource_path(self, relative_path):
        """Get absolute path to resource, works for dev and PyInstaller."""
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)
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
            while not self.ui_queue.empty():
                msg, args = self.ui_queue.get_nowait()
                if msg == "update_progress":
                    progress_var, value = args
                    progress_var.set(value)
                elif msg == "update_status":
                    status_label, text = args
                    status_label.config(text=text)
                elif msg == "update_speed":
                    speed_label, text = args
                    speed_label.config(text=text)
                elif msg == "reset_ui":
                    self.reset_ui()
        except queue.Empty:
            pass
        self.after(100, self.process_ui_queue)
    def build_gui(self):
        # Main container
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
      
        # Left: Box art + details + Patch Button
        left_frame = tk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)
      
        self.img_label = tk.Label(left_frame, bg="#222", text="No Image")
        self.img_label.pack(pady=10)
      
        details_frame = tk.Frame(left_frame)
        details_frame.pack(fill=tk.BOTH, expand=True)
      
        self.dev_label = tk.Label(details_frame, text="", font=("Arial", 10, "bold"))
        self.dev_label.pack(anchor="w")
        self.pub_label = tk.Label(details_frame, text="")
        self.pub_label.pack(anchor="w")
        self.notes_label = tk.Label(details_frame, text="", wraplength=220)
        self.notes_label.pack(anchor="w")
        self.status_label = tk.Label(details_frame, text="", fg="green")
        self.status_label.pack(anchor="w")
      
        # Patch button moved here, below details
        self.patch_btn = tk.Button(details_frame, text="Patch Selected Game", command=self.patch)
        self.patch_btn.pack(anchor="w", pady=(10, 0))
      
        self.open_folder_btn = tk.Button(details_frame, text="Open Game Folder", command=self.open_folder, state=tk.DISABLED)
        self.open_folder_btn.pack(anchor="w", pady=(5, 0))
      
        self.launch_btn = tk.Button(details_frame, text="Launch Game", command=self.launch_game, state=tk.DISABLED)
        self.launch_btn.pack(anchor="w", pady=(5, 0))
      
        # Right: Game list
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
      
        # Search bar
        search_frame = tk.Frame(right_frame)
        search_frame.pack(fill=tk.X, padx=0, pady=(0, 5))
        tk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind('<KeyRelease>', self.filter_games)
      
        # Treeview for games (only name)
        cols = ("Game",)
        self.tree = ttk.Treeview(right_frame, columns=cols, show="headings")
        self.tree.heading("Game", text="Game")
        self.tree.column("Game", width=400)
        self.tree.pack(fill=tk.BOTH, expand=True)
      
        # Initially populate sorted list
        for match in self.matches:
            appid = str(match["data"]["appid"]).strip()
            self.tree.insert("", "end",
                values=(match["game_name"],),
                tags=(appid,)
            )
            logging.debug(f"TAG: {appid}")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
      
        # Bottom status (now only DB status, no button)
        bottom_frame = tk.Frame(self)
        bottom_frame.pack(fill=tk.X, pady=5)
      
        self.status = tk.Label(bottom_frame, text=self.db_status, anchor="w", fg="green")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
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
        logging.debug(f"SELECTED: '{appid}'")
        match = self.by_id.get(appid)
        if not match:
            logging.warning(f"NOT FOUND: {appid}")
            self.clear_details()
            return
        logging.debug(f"FOUND: {match['game_name']}")
        # Load box art
        img = load_box_art(self.steam_path, appid)
        if img:
            self.img_label.configure(image=img, text="")
            self.img_label.image = img
            logging.debug("BOX ART LOADED")
        else:
            self.img_label.configure(image="", text="No box art")
        # Update details
        self.dev_label.config(text=f"Developer: {match['dev_name']}")
        self.pub_label.config(text=f"Publisher: {match['data'].get('publisher', 'N/A')}")
        self.notes_label.config(text=f"Notes: {match['data'].get('notes', 'N/A')}")
        self.status_label.config(text=f"Status: {match['data'].get('store_status', 'N/A')}")
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
        self.patch_btn.config(state="normal", text="Patch Selected Game")
        self.status.config(text=self.db_status, fg="green")
        if self.progress_frame:
            self.progress_frame.destroy()
            self.progress_frame = None
    def parse_size_bytes(self, size_str):
        """Parse size string like '199.7 MB' to bytes."""
        if not size_str or size_str == 'Unknown':
            return None
        try:
            if 'MB' in size_str:
                return int(float(size_str.replace(' MB', '')) * 1024 * 1024)
            elif 'KB' in size_str:
                return int(float(size_str.replace(' KB', '')) * 1024)
            elif size_str.isdigit():
                return int(size_str)
            return None
        except:
            return None
    def download_with_gdown(self, file_id, output_path, expected_bytes, progress_var, status_label, speed_label):
        """Download using gdown with console progress."""
        try:
            import gdown
            self.ui_queue.put(("update_status", (status_label, f"Downloading with gdown: {output_path.name}")))
            gdown.download(id=file_id, output=str(output_path), quiet=False, resume=True) # quiet=False for console tqdm
            actual_size = os.path.getsize(output_path)
            logging.info(f"gdown downloaded {actual_size} bytes")
            return actual_size
        except Exception as e:
            logging.error(f"gdown failed: {e}")
            raise e
    def extract_with_7z(self, archive_path, extract_dir):
        """Extract archive using local 7z.exe via subprocess."""
        script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        local_7z = script_dir / '7z.exe'
        if not local_7z.exists():
            raise FileNotFoundError("7z.exe not found in script directory.")
        
        # DEBUG: Log paths
        logging.debug(f"DEBUG: Archive path: {archive_path} (exists: {archive_path.exists()})")
        logging.debug(f"DEBUG: Extract dir: {extract_dir} (exists: {extract_dir.exists()}, is_dir: {extract_dir.is_dir()})")
        if not extract_dir.is_dir():
            extract_dir.mkdir(parents=True, exist_ok=True)
            logging.debug(f"DEBUG: Created extract dir manually.")
        
        # Win11 Safety: Strip any anomalous suffix like .exe
        if extract_dir.suffix == '.exe':
            extract_dir = extract_dir.with_suffix('')
            extract_dir.mkdir(exist_ok=True)
            logging.debug(f"DEBUG: Stripped .exe suffix to: {extract_dir}")
        
        cmd = [str(local_7z), 'x', str(archive_path), f'-o{extract_dir}', '-y'] # -y to auto-yes
        logging.debug(f"DEBUG: Full CMD: {' '.join(f'\"{a}\"' if ' ' in a else a for a in cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"7z extraction failed: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
        logging.info(f"Extracted with 7z: {archive_path} to {extract_dir}")
    def smart_apply_patch(self, extract_dir, install_dir, status_label):
        """Scan and match files from extracted patch to game dir, with hybrid overwrite/add logic."""
        # Build map of game files: filename.lower() -> list of full_paths
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
    def process_patch(self, files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label):
        """Thread worker for download/extract/smart apply."""
        try:
            # Find local 7z.exe
            script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
            local_7z = script_dir / '7z.exe'
            if not local_7z.exists():
                raise FileNotFoundError("7z.exe not found. Please download from https://www.7-zip.org/ and place in script directory.")
            logging.info(f"Using local 7z.exe: {local_7z}")
            
            for idx in selected_indices:
                f = files[idx]
                file_id = f['id']
                file_path = f.get('path', f['name'])
                file_name = f['name']
                expected_bytes = self.parse_size_bytes(f.get('size'))
                
                # Cache logic: Build cache path (handles subdirs)
                cache_file = self.cache_dir / file_name
                cache_file.parent.mkdir(parents=True, exist_ok=True)  # Create nested dirs if needed
                
                use_cache = False
                if cache_file.exists():
                    actual_size = os.path.getsize(cache_file)
                    # Size verify: exact match or tolerance for small/unknown
                    small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                    tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                    
                    # Integrity test for cached file
                    test_cmd = [str(local_7z), 't', str(cache_file)]
                    logging.info(f"Testing cached file integrity: {cache_file}")
                    test_result = subprocess.run(test_cmd, capture_output=True, text=True)
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
                
                retries = 0
                max_retries = 3
                output = cache_file  # Download/extract from/to cache
                
                if not use_cache:
                    while retries < max_retries:
                        logging.info(f"Downloading {file_path} (attempt {retries+1}/{max_retries})")
                        self.ui_queue.put(("update_status", (status_label, f"Downloading: {file_path}")))
                        self.ui_queue.put(("update_progress", (progress_var, -1)))  # Indeterminate
                        downloaded_bytes = self.download_with_gdown(file_id, output, expected_bytes or 0, progress_var, status_label, speed_label)
                        actual_size = os.path.getsize(output)
                        logging.info(f"gdown downloaded {actual_size} bytes")
                        
                        # Size verify (relaxed check)
                        small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                        tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                        if tolerance_check or small_file_check:
                            logging.info(f"Download size verified: {file_path} ({actual_size} bytes)")
                            
                            # Integrity test post-download (for RAR/7z/ZIP)
                            if output.suffix.lower() in ['.rar', '.7z', '.zip']:
                                test_cmd = [str(local_7z), 't', str(output)]
                                logging.info(f"Testing downloaded archive integrity: {output}")
                                test_result = subprocess.run(test_cmd, capture_output=True, text=True)
                                if test_result.returncode != 0:
                                    logging.error(f"Downloaded archive failed integrity test: {test_result.stderr}. Keeping file for manual repair (no auto-delete).")
                                    # Don't raise here—let user decide (file stays)
                                    # But for now, raise to trigger error UI
                                    raise RuntimeError(f"Downloaded archive failed integrity (7z t): {test_result.stderr}. File kept in cache—try manual repair with WinRAR or re-download.")
                            
                            logging.info(f"Download and integrity verified: {file_path}")
                            break
                        
                        retries += 1
                        logging.warning(f"Size mismatch: expected {expected_bytes or 'Unknown'}, got {actual_size}. Retrying...")
                        if output.exists():
                            output.unlink()  # Clean partial download
                            
                    if retries >= max_retries:
                        raise ValueError(f"Download size mismatch after {max_retries} retries for {file_path}.")
                else:
                    self.ui_queue.put(("update_status", (status_label, f"Using cached patch: {file_path}")))
                    self.ui_queue.put(("update_progress", (progress_var, 0)))  # No progress needed
                
                # Extract to UNIQUE temp dir
                self.ui_queue.put(("update_status", (status_label, f"Extracting: {file_path}")))
                temp_extract_dir = Path(tempfile.mkdtemp())
                logging.info(f"Extracting {output} to {temp_extract_dir}")
                try:
                    if output.suffix.lower() == ".exe":
                        # Enhanced flags for stubborn self-extractors
                        cmd = [str(output), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']  # Common Inno/NSIS flags
                        result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True)
                        if result.returncode != 0:
                            # Fallback to /S
                            cmd = [str(output), '/S']
                            result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True)
                        if result.returncode != 0:
                            # Final fallback: no args
                            result = subprocess.run([str(output)], cwd=str(temp_extract_dir), capture_output=True, text=True)
                        if result.returncode != 0:
                            logging.error(f"All EXE extraction attempts failed: {result.stderr}. File kept in cache—run manually by double-clicking {output}.")
                            raise RuntimeError(f"Self-extracting EXE failed all modes: {result.stderr}. File kept in cache—run manually.")
                        
                        files_extracted = os.listdir(temp_extract_dir)
                        logging.info(f"Files after EXE run: {files_extracted}")
                        if not files_extracted:
                            logging.warning("No files extracted by EXE—may need manual run. File kept in cache.")
                            # Don't raise—allow apply (0 files OK), but warn in UI later
                    else:
                        # Use 7z for ZIP, 7Z, RAR (integrity already checked above)
                        self.extract_with_7z(output, temp_extract_dir)
                finally:
                    # Do NOT unlink output—keep in cache for future use / manual repair!
                    pass
                
                # Smart apply
                self.ui_queue.put(("update_status", (status_label, f"Applying: {file_path}")))
                overwritten, added, skipped = self.smart_apply_patch(temp_extract_dir, install_dir, status_label)
                logging.info(f"Applied: {overwritten} overwritten, {added} added, {skipped} skipped")
                # Clean up temp extract dir only
                shutil.rmtree(temp_extract_dir)
                logging.info(f"Completed {file_path}")
            
            self.ui_queue.put(("update_status", (status_label, "SUCCESS")))
            self.after(100, lambda: messagebox.showinfo("SUCCESS", f"Patched:\n{game_name}\n\nApplied: {len(selected_indices)} files"))
        except Exception as e:
            error_msg = str(e)
            if "7z.exe not found" in error_msg:
                error_msg += "\n\nDownload 7-Zip from https://www.7-zip.org/ and place 7z.exe in the script directory."
            elif "Extraction failed" in error_msg:
                error_msg += "\n\nThe archive may be corrupted or requires manual extraction. Try re-downloading and running manually."
            elif "gdown failed" in error_msg:
                error_msg += "\n\ngdown error. Ensure gdown is installed and try manual download."
            elif "integrity" in error_msg.lower():
                error_msg += "\n\nFile kept in cache for manual repair. Use WinRAR or re-download via browser."
            elif "EXE failed" in error_msg:
                error_msg += "\n\nFile kept in cache—try running the EXE manually by double-clicking it."
            
            # NO auto-cleanup on failure—keep file for debugging/manual use!
            # if 'output' in locals() and output.exists() and not use_cache:
            #     output.unlink()  # Commented out to prevent "vanish"
            #     logging.info(f"Cleaned partial cache: {output}")
            
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
        # Prepare list for dialog (path + size)
        display_files = []
        for f in files:
            size_str = f.get('size', 'Unknown')
            file_path = f.get('path', f['name'])
            display_files.append(f"{file_path} ({size_str})")
        if not messagebox.askyesno("APPLY PATCH", f"Patch:\n{game_name}\n\nTo:\n{install_dir}\n\nContinue?"):
            return
        self.patch_btn.config(state="disabled", text="PREPARING...")
        self.status.config(text="Loading patch options...", fg="orange")
        self.update()
        # Show selection dialog
        dialog = PatchSelectionDialog(self, display_files)
        self.wait_window(dialog)
        selected_indices = dialog.result
        if not selected_indices:
            self.reset_ui()
            return
        # Now proceed with download/extract in thread
        self.status.config(text="Downloading selected patches...", fg="blue")
        self.update()
        # Create progress UI (indeterminate for gdown)
        self.progress_frame = tk.Frame(self)
        self.progress_frame.pack(fill=tk.X, padx=10, pady=5)
        progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(self.progress_frame, variable=progress_var, maximum=100, mode='indeterminate')
        progress_bar.pack(fill=tk.X, pady=2)
        progress_bar.start() # Start indeterminate
        status_label = tk.Label(self.progress_frame, text="Starting download...")
        status_label.pack()
        speed_label = tk.Label(self.progress_frame, text="See console for progress")
        speed_label.pack()
        # Start thread
        threading.Thread(target=self.process_patch, args=(files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label), daemon=True).start()

if __name__ == "__main__":
    setup_logging()  # Initialize logging to file and console
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
