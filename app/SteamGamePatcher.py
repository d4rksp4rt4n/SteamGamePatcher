import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
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
from collections import defaultdict

def log(*args):
    print("[DEBUG]", *args)

def ensure_7z_exe():
    """Extract 7z.exe alongside the app if not present."""
    # Get the directory where the EXE is running
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).parent
    
    seven_zip = app_dir / '7z.exe'
    
    if seven_zip.exists():
        log("7z.exe already available alongside app")
        return
    
    log("Extracting 7z.exe from bundle...")
    try:
        # Extract from PyInstaller bundle
        import importlib.resources
        bundled_7z = importlib.resources.files('__main__').joinpath('7z.exe')
        shutil.copy2(bundled_7z, seven_zip)
        log(f"7z.exe extracted to {seven_zip}")
    except Exception as e:
        log(f"Failed to extract 7z.exe: {e}")
        messagebox.showwarning("Missing 7z.exe", "7z.exe not found. Download from https://www.7-zip.org and place in the app folder.")
        sys.exit(1)

def get_steam_path():
    log("Finding Steam...")
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Valve\Steam")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        winreg.CloseKey(key)
        log(f"Steam: {path}")
        return Path(path)
    except:
        p = Path(os.getenv("ProgramFiles(x86)")) / "Steam"
        if p.exists():
            log(f"Steam fallback: {p}")
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
                                log(f"Game: {appid} -> {full}")
                            break
            except:
                pass
    log(f"Installed: {len(installed)}")
    return installed

def load_box_art(steam_path, appid):
    """ULTIMATE 2025 Steam box art loader — works with deleted games, custom .jpg grid, deep hash folders"""
    appid = str(appid)
    log(f"\n=== BOX ART SEARCH FOR APPID: {appid} ===")
    log(f"Steam path: {steam_path}")

    cache_dir = steam_path / "appcache" / "librarycache"
    userdata_dir = steam_path / "userdata"

    candidates = []
    custom_grid = []

    # 1. Modern flat files
    for ext in ["jpg", "jpeg", "png"]:
        p = cache_dir / f"{appid}_library_600x900.{ext}"
        if p.exists():
            candidates.append(p)
            log(f"FOUND flat 600x900: {p.name}")

    # 2. Legacy: deep scan ALL subfolders (handles hash-in-hash)
    legacy_root = cache_dir / appid
    if legacy_root.exists() and legacy_root.is_dir():
        log(f"Scanning legacy root: {legacy_root}")
        for root, dirs, files in os.walk(legacy_root):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png')):
                    filepath = Path(root) / file
                    name = file.lower()
                    if any(k in name for k in ["library_600x900", "capsule", "header", "hero"]):
                        candidates.append(filepath)
                        log(f"FOUND in subfolder: {filepath.relative_to(cache_dir)}")

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
                        log(f"FOUND CUSTOM GRID: {grid_file.name} (user: {user.name})")
                        break

    # === PICK BEST ===
    all_images = candidates + custom_grid
    if not all_images:
        log("NO BOX ART FOUND ANYWHERE")
        log("=== END SEARCH ===\n")
        return None

    # CUSTOM GRID ALWAYS WINS
    if custom_grid:
        best = max(custom_grid, key=lambda x: x.stat().st_mtime)
        log(f"WINNER: CUSTOM GRID -> {best.name}")
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
                log(f"WINNER: PRIORITY -> {best.name}")
                break
        if not best:
            best = max(candidates, key=lambda x: x.stat().st_mtime)
            log(f"FALLBACK -> {best.name}")

    # Load
    try:
        img = Image.open(best).convert("RGB")
        log(f"Loaded: {img.size} -> {best.name}")
        img.thumbnail((200, 300), Image.Resampling.LANCZOS)
        bg = Image.new("RGB", (200, 300), (28, 28, 38))
        offset = ((200 - img.width) // 2, (300 - img.height) // 2)
        bg.paste(img, offset)
        photo = ImageTk.PhotoImage(bg)
        log("BOX ART LOADED PERFECTLY")
        log("=== END SEARCH ===\n")
        return photo
    except Exception as e:
        log(f"FAILED: {e}")
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

# Main App
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Steam Game Patcher")
        self.geometry("960x640")
        # Auto-download database
        DB_URL = "https://raw.githubusercontent.com/d4rksp4rt4n/SteamGamePatcher/main/data/patches_database.json"
        DB_PATH = Path('data/patches_database.json')

        def download_database():
            try:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                r = requests.get(DB_URL, timeout=15)
                r.raise_for_status()
                with open(DB_PATH, 'w', encoding='utf-8') as f:
                    f.write(r.text)
                log("Database auto-updated from GitHub")
                return True
            except Exception as e:
                log(f"Auto-update failed (using local): {e}")
                return False

        # Download if needed
        updated = download_database() if not DB_PATH.exists() or (time.time() - DB_PATH.stat().st_mtime > 3600) else False

        if not DB_PATH.exists():
            messagebox.showerror("No Database", "Download failed. Check internet.")
            sys.exit(1)

        with open(DB_PATH, 'r', encoding='utf-8') as f:
            self.folder_db = json.load(f)
        
        # Check for meta and set status
        meta = self.folder_db.get('meta', {})
        last_updated = meta.get('last_updated', 'Unknown')
        is_incremental = meta.get('incremental', False)
        is_unified = meta.get('unified', False)
        
        db_status = "Updated" if updated else "Up to date"
        db_note = ""
        if is_incremental and is_unified:
            db_note = " (incremental + unified)"
        elif is_incremental:
            db_note = " (incremental)"
        elif is_unified:
            db_note = " (unified)"
        
        self.db_status = f"DB: {db_status} - {last_updated}{db_note}"
        
        self.folder_map = self.folder_db.get('developers', {})
        steam = get_steam_path()
        if not steam:
            messagebox.showerror("Error", "Steam not found")
            sys.exit(1)
        self.installed = get_installed_games(steam)
        self.steam_path = steam  # For box art
        # Build matches from unified folder_db using appid
        self.matches = []
        self.by_id = {}  # appid -> {"dev_name": , "game_name": , "data": game_data}
        for dev_name, dev_data in self.folder_map.items():
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
                        log(f"MATCH: {appid} -> {game_name} by {dev_name}")
        log(f"FOUND {len(self.matches)} matched games with patches")
        self.build_gui()
        if self.matches:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.on_select(None)
        self.progress_frame = None
        self.ui_queue = queue.Queue()
        self.after(100, self.process_ui_queue)

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
        
        # Left: Box art + details
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
        
        # Right: Game list
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Treeview for games (only name)
        cols = ("Game",)
        self.tree = ttk.Treeview(right_frame, columns=cols, show="headings")
        self.tree.heading("Game", text="Game")
        self.tree.column("Game", width=400)
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        for match in self.matches:
            appid = str(match["data"]["appid"]).strip()
            self.tree.insert("", "end",
                values=(match["game_name"],),
                tags=(appid,)
            )
            log(f"TAG: {appid}")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        
        # Bottom status and button
        bottom_frame = tk.Frame(self)
        bottom_frame.pack(fill=tk.X, pady=5)
        
        self.status = tk.Label(bottom_frame, text=f"{self.db_status} | FOUND {len(self.matches)} game(s)", anchor="w", fg="green")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        
        self.patch_btn = tk.Button(bottom_frame, text="Patch Selected Game", command=self.patch)
        self.patch_btn.pack(side=tk.RIGHT, padx=10)

    def on_select(self, _):
        selected = self.tree.selection()
        if not selected:
            return
        tags = self.tree.item(selected[0])["tags"]
        if not tags:
            return
        appid = str(tags[0])
        log(f"SELECTED: '{appid}'")
        match = self.by_id.get(appid)
        if not match:
            log(f"NOT FOUND: {appid}")
            self.img_label.configure(image="", text="No data")
            self.dev_label.config(text="")
            self.pub_label.config(text="")
            self.notes_label.config(text="")
            self.status_label.config(text="")
            return
        log(f"FOUND: {match['game_name']}")
        # Load box art
        img = load_box_art(self.steam_path, appid)
        if img:
            self.img_label.configure(image=img, text="")
            self.img_label.image = img
            log("BOX ART LOADED")
        else:
            self.img_label.configure(image="", text="No box art")
        # Update details
        self.dev_label.config(text=f"Developer: {match['dev_name']}")
        self.pub_label.config(text=f"Publisher: {match['data'].get('publisher', 'N/A')}")
        self.notes_label.config(text=f"Notes: {match['data'].get('notes', 'N/A')}")
        self.status_label.config(text=f"Status: {match['data'].get('store_status', 'N/A')}")

    def reset_ui(self):
        self.patch_btn.config(state="normal", text="Patch Selected Game")
        self.status.config(text=f"{self.db_status} | FOUND {len(self.matches)} game(s)", fg="green")
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
            gdown.download(id=file_id, output=str(output_path), quiet=False, resume=True)  # quiet=False for console tqdm
            actual_size = os.path.getsize(output_path)
            log(f"gdown downloaded {actual_size} bytes")
            return actual_size
        except Exception as e:
            log(f"gdown failed: {e}")
            raise e

    def extract_with_7z(self, archive_path, extract_dir):
        """Extract archive using local 7z.exe via subprocess."""
        script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        local_7z = script_dir / '7z.exe'
        if not local_7z.exists():
            raise FileNotFoundError("7z.exe not found in script directory.")
        cmd = [str(local_7z), 'x', str(archive_path), f'-o{extract_dir}', '-y']  # -y to auto-yes
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log(f"7z extraction failed: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
        log(f"Extracted with 7z: {archive_path} to {extract_dir}")

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
                        log(f"MULTIPLE MATCHES for {file}: {matches} - Skipping")
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
            log(f"Using local 7z.exe: {local_7z}")

            for idx in selected_indices:
                f = files[idx]
                file_id = f['id']
                file_name = f['name']
                expected_bytes = self.parse_size_bytes(f.get('size'))
                retries = 0
                max_retries = 3
                output = None
                while retries < max_retries:
                    log(f"Downloading {file_name} (attempt {retries+1}/{max_retries})")
                    output = Path(tempfile.gettempdir()) / file_name
                    self.ui_queue.put(("update_status", (status_label, f"Downloading: {file_name}")))
                    # Set indeterminate progress during gdown
                    self.ui_queue.put(("update_progress", (progress_var, -1)))  # Indeterminate mode
                    downloaded_bytes = self.download_with_gdown(file_id, output, expected_bytes or 0, progress_var, status_label, speed_label)
                    actual_size = os.path.getsize(output)
                    # Relaxed check: for small files (<2KB), accept if >0 bytes; else 5% tolerance
                    small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                    tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                    if tolerance_check or small_file_check:
                        break
                    retries += 1
                    log(f"Size mismatch: expected {expected_bytes or 'Unknown'}, got {actual_size}. Retrying...")
                    if output.exists():
                        output.unlink()
                if retries >= max_retries:
                    raise ValueError(f"Download size mismatch after {max_retries} retries.")
                
                self.ui_queue.put(("update_progress", (progress_var, 0)))  # Reset for next
                self.ui_queue.put(("update_status", (status_label, f"Extracting: {file_name}")))
                # Extract to UNIQUE temp dir OUTSIDE install_dir
                temp_extract_dir = Path(tempfile.mkdtemp())
                log(f"Extracting {output} to {temp_extract_dir}")
                try:
                    if output.suffix.lower() == ".exe":
                        # For self-extracting EXE, try silent mode if possible
                        cmd = [str(output), '/S']  # Try /S for silent
                        result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True)
                        if result.returncode != 0:
                            # Fallback to no args
                            result = subprocess.run([str(output)], cwd=str(temp_extract_dir), capture_output=True, text=True)
                        if result.returncode != 0:
                            log(f"EXE extraction failed: {result.stderr}")
                            raise RuntimeError(f"Self-extracting EXE failed: {result.stderr}")
                        # Log files after extraction
                        files_extracted = os.listdir(temp_extract_dir)
                        log(f"Files after EXE run: {files_extracted}")
                        if not files_extracted:
                            log("Warning: No files extracted by EXE - may require manual run or different args")
                    else:
                        # Use 7z for ZIP, 7Z, RAR
                        self.extract_with_7z(output, temp_extract_dir)
                finally:
                    if output and output.exists():
                        output.unlink()  # Clean up archive
                
                # Smart apply
                self.ui_queue.put(("update_status", (status_label, f"Applying: {file_name}")))
                overwritten, added, skipped = self.smart_apply_patch(temp_extract_dir, install_dir, status_label)
                log(f"Applied: {overwritten} overwritten, {added} added, {skipped} skipped")
                # Clean up temp
                shutil.rmtree(temp_extract_dir)
                log(f"Completed {file_name}")
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
            self.ui_queue.put(("update_status", (status_label, "FAILED")))
            log(f"PATCH FAILED: {error_msg}")
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
        # Prepare list for dialog (name + size)
        display_files = []
        for f in files:
            size_str = f.get('size', 'Unknown')
            display_files.append(f"{f['name']} ({size_str})")
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
        progress_bar.start()  # Start indeterminate
        status_label = tk.Label(self.progress_frame, text="Starting download...")
        status_label.pack()
        speed_label = tk.Label(self.progress_frame, text="See console for progress")
        speed_label.pack()
        # Start thread
        threading.Thread(target=self.process_patch, args=(files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label), daemon=True).start()

if __name__ == "__main__":
    ensure_7z_exe()  # Run this first for standalone
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
