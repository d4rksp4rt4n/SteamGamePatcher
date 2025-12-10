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
import re # for progress parsing

# --- Imports for Enhanced DOCX Rendering ---
try:
    from docx import Document
    from docx.document import Document as _Document
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.table import _Cell, Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn  # <--- CRITICAL ADDITION FOR XML PARSING
except ImportError:
    pass

APP_VERSION = '1.34-beta'
CONFIG_FILENAME = 'patcher_config.json'  # Per-game config file

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

def get_app_font(size=10, weight="normal", slant="roman"):
    """
    Retrieves the best available font (Roboto -> System -> Arial)
    Supports weight (normal/bold) and slant (roman/italic).
    """
    candidates = ["Roboto", "Segoe UI", "Calibri", "Arial", "Helvetica", "sans-serif"]
    
    # Try finding an installed system font from the list
    for family in candidates:
        try:
            # Check if font exists by creating a temporary font object
            font = tkfont.Font(family=family, size=size, weight=weight, slant=slant)
            # .actual() checks if the system actually used the requested family
            if font.actual()['family'].lower() == family.lower():
                logging.info(f"FONT: Using {family} (Size: {size}, Weight: {weight}, Slant: {slant})")
                return font
        except:
            continue
            
    # Fallback to generic Arial if nothing else matches specific criteria
    return tkfont.Font(family="Arial", size=size, weight=weight, slant=slant)

def ensure_7z_exe():
    """Extract 7z.exe and 7z.dll alongside the app if not present."""
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
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Wow6432Node\\Valve\\Steam")
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
        main_app = parent.get_main_app() if hasattr(parent, "get_main_app") else parent
        main_app.center_window(self, 700, 600)
        self.main_app = main_app
        self.result = None
        self.file_entries = file_entries # This list is already sorted by App.patch
        self.viewable_exts = ('.txt', '.docx', '.pdf')
        
        # Define colors and tracking for hover effect
        self.color_viewable = "#4CAF50" # Green (Original color)
        self.color_viewable_hover = "#90CAF9" # Light Blue (Simulated underline/highlight)
        self.color_binary = "#E0E0E0"  # Light gray (default)
        self.hovered_index = -1 # Track the index currently being hovered
        
        tk.Label(self,
                 text="Select patches to apply\nInstructions (colored) can be viewed via single-click",
                 font=get_app_font(11, "bold")).pack(pady=12)
        
        frame = tk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        
        self.listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, font=get_app_font(10), bg="#222222", fg=self.color_binary, selectbackground="#424242", selectforeground="white")
        scrollbar = tk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # FIX: Use itemconfig() for coloring individual Listbox items
        for i, line in enumerate(display_files):
            file_name = self.file_entries[i]['name'].lower()
            fg_color = self.color_viewable if file_name.endswith(self.viewable_exts) else self.color_binary
            
            self.listbox.insert(tk.END, line)
            # Apply color using itemconfig (the correct method for tk.Listbox)
            self.listbox.itemconfig(i, {'fg': fg_color})
            
        self.listbox.bind("<<ListboxSelect>>", self.on_selection_change)
        self.listbox.bind('<Button-1>', self.on_single_click) 
        self.listbox.bind('<Motion>', self.on_motion) # Motion Binding for Hover/Cursor Change
        
        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=15)
        self.apply_btn = tk.Button(btn_frame, text="Apply Selected Patches",
                                   command=self.apply, bg="#b52f2f", fg="white",
                                   font=get_app_font(10, "bold"), state=tk.DISABLED)
        self.apply_btn.pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="Cancel", command=self.on_closing).pack(side=tk.LEFT, padx=10)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.on_selection_change()

    def on_selection_change(self, event=None):
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            self.apply_btn.config(state=tk.DISABLED, text="Apply Selected Patches")
            return
            
        # Check if ALL selected files are instruction files
        all_viewable = all(
            self.file_entries[i]['name'].lower().endswith(self.viewable_exts)
            for i in selected_indices
        )
        
        if all_viewable:
            self.apply_btn.config(state=tk.DISABLED, text="Instructions only (Click list to view)")
        else:
            self.apply_btn.config(state=tk.NORMAL, text="Apply Selected Patches")

    def on_single_click(self, event=None):
        """
        Handle single-click event for viewing or selection.
        Viewable files are explicitly unselected if clicked.
        """
        try:
            # Determine the index clicked based on the event's y-coordinate
            idx = self.listbox.nearest(event.y)
        except:
            return

        if idx >= len(self.file_entries): return
        f = self.file_entries[idx]
        name = f['name'].lower()
        
        # Check if the file is viewable
        if name.endswith(self.viewable_exts):
            
            # --- Primary Fix: Prevent selection and clear selection ---
            # If the item is already selected, clear it. This ensures instructions 
            # are view-only and cannot contribute to the final patch list.
            if idx in self.listbox.curselection():
                self.listbox.selection_clear(idx)
            
            # Open the instructions dialog
            InstructionsDialog(self, f)
            
            # Update button state based on the final selection (which should now exclude this item)
            self.on_selection_change()
            
            # Stop further processing (prevents default selection toggle)
            return "break"
            
        else:
            # For patch files, allow the default selection behavior to occur 
            # (which happens automatically after this handler finishes).
            pass
            
    def on_motion(self, event):
        """Dynamically change cursor and foreground color if hovering over a viewable file (simulating underline)."""
        try:
            # 1. Get the index currently under the mouse
            idx = self.listbox.nearest(event.y)
            
            # 2. Check if the index is valid and the file is viewable
            is_viewable = False
            if 0 <= idx < len(self.file_entries):
                file_name = self.file_entries[idx]['name'].lower()
                is_viewable = file_name.endswith(self.viewable_exts)

            # 3. Check for change in hover state
            if idx != self.hovered_index:
                
                # A. Reset the color of the PREVIOUSLY hovered item if it was viewable
                if 0 <= self.hovered_index < len(self.file_entries):
                    prev_name = self.file_entries[self.hovered_index]['name'].lower()
                    if prev_name.endswith(self.viewable_exts):
                        self.listbox.itemconfig(self.hovered_index, {'fg': self.color_viewable})
                
                # B. Apply hover color to the NEWLY hovered item if it is viewable
                if is_viewable:
                    self.listbox.itemconfig(idx, {'fg': self.color_viewable_hover})

                # C. Update tracked index
                self.hovered_index = idx
            
            # 4. Set the cursor based on whether the current item is viewable
            if is_viewable:
                self.listbox.config(cursor="hand2")
            else:
                self.listbox.config(cursor="")

        except tk.TclError:
            # Mouse moved outside the Listbox boundaries
            if self.hovered_index != -1:
                # Reset the color of the last hovered item before exiting
                if 0 <= self.hovered_index < len(self.file_entries):
                    prev_name = self.file_entries[self.hovered_index]['name'].lower()
                    if prev_name.endswith(self.viewable_exts):
                        self.listbox.itemconfig(self.hovered_index, {'fg': self.color_viewable})
            self.hovered_index = -1
            self.listbox.config(cursor="")


    def apply(self):
        indices = self.listbox.curselection()
        
        # Filter out instruction files from the selection before applying
        self.result = [
            i for i in indices 
            if not self.file_entries[i]['name'].lower().endswith(self.viewable_exts)
        ]
        
        # Check if they only selected instruction files (which shouldn't happen 
        # if on_selection_change works correctly, but acts as a safeguard)
        if not self.result and indices:
             messagebox.showinfo("Nothing to Apply", "You only selected instructions. Please select a binary file to apply a patch.")
             self.result = None
             return
             
        self.destroy()

    def on_closing(self):
        try:
            if self.main_app and self.main_app.winfo_exists():
                self.main_app.reset_ui()
        except: pass
        self.destroy()

class InstructionsDialog(tk.Toplevel):
    def __init__(self, parent, file_data):
        super().__init__(parent)
        self.title(f"Instructions: {file_data['name']}")
        self.geometry("1000x800") 

        # Center Window logic
        try:
            main_app = parent.main_app if hasattr(parent, "main_app") else parent
            main_app.center_window(self, 1000, 800) 
        except:
            pass

        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.temp_images = []
        self.image_refs = []
        self.thread_content = None # Stores content from the thread
        self.thread_error = None   # Stores error from the thread
        self.temp_file = None      # To be set by the thread
        
        # Base colors
        BG_COLOR = "#1e1e1e"
        FG_COLOR = "#e0e0e0"

        # Header Frame
        header_frame = tk.Frame(self, bg="#121212")
        header_frame.pack(fill=tk.X)
        
        tk.Label(
            header_frame,
            text=file_data.get('path', file_data['name']),
            font=get_app_font(13, "bold"),
            fg="#4FC3F7",
            bg="#121212",
            pady=10
        ).pack(fill=tk.X)

        # --- UNIFIED CONTENT WIDGET (tk.Text) ---
        frame = tk.Frame(self, bg=BG_COLOR)
        frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text_widget = tk.Text(
            frame,
            wrap=tk.WORD,
            bg=BG_COLOR,
            fg=FG_COLOR,
            font=get_app_font(11),
            relief="flat",
            bd=0,
            padx=20,
            pady=20,
            yscrollcommand=scrollbar.set
        )
        self.text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text_widget.yview)

        # Configure formatting tags
        self.text_widget.tag_configure("bold", font=get_app_font(11, weight="bold"))
        self.text_widget.tag_configure("italic", font=get_app_font(11, slant="italic"))
        self.text_widget.tag_configure("heading", font=get_app_font(16, weight="bold"), spacing1=10, spacing3=10, foreground="#90CAF9")
        self.text_widget.tag_configure("link", foreground="#64B5F6", underline=1)
        self.text_widget.tag_bind("link", "<Enter>", lambda e: self.text_widget.config(cursor="hand2"))
        self.text_widget.tag_bind("link", "<Leave>", lambda e: self.text_widget.config(cursor=""))

        # --- NEW: Show Loading, Start Thread ---
        self._show_loading()
        self.loading_thread = threading.Thread(target=self._load_content_async, args=(file_data,))
        self.loading_thread.start()
        self._check_thread(file_data) # Start polling for thread completion
        # ----------------------------------------

        # Close button
        tk.Button(
            self,
            text="Close",
            command=self.destroy,
            font=get_app_font(12, "bold"),
            bg="#e53935",
            fg="white",
            relief="flat",
            padx=30,
            pady=10,
            cursor="hand2"
        ).pack(pady=20)

    # --- NEW LOADING METHODS ---
    def _show_loading(self):
        """Displays the loading animation over the content area."""
        BG_COLOR = "#1e1e1e"
        self.loader_frame = tk.Frame(self, bg=BG_COLOR, width=1000, height=800)
        self.loader_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER, relwidth=1.0, relheight=1.0)
        
        # Use ttk.Progressbar for a modern loading spinner
        self.progress_bar = ttk.Progressbar(self.loader_frame, mode='indeterminate', length=200)
        self.progress_bar.pack(pady=100, padx=20)
        self.progress_bar.start(10)

        tk.Label(
            self.loader_frame, 
            text="Downloading and Processing Document...", 
            font=get_app_font(14), 
            fg="#64B5F6", 
            bg=BG_COLOR
        ).pack()

    def _hide_loading(self):
        """Hides and destroys the loading animation frame."""
        if hasattr(self, 'progress_bar') and self.progress_bar:
            self.progress_bar.stop()
            self.progress_bar.destroy()
        if hasattr(self, 'loader_frame') and self.loader_frame:
            self.loader_frame.destroy()

    def _load_content_async(self, file_data):
        """Performs blocking file I/O (download/copy/read) in a separate thread."""
        file_id = file_data.get('id', None)
        file_name = file_data['name'].lower()
        self.temp_file = Path(tempfile.gettempdir()) / f"instr_{uuid.uuid4().hex}"
        
        try:
            if 'path' in file_data and Path(file_data['path']).exists():
                 import shutil
                 shutil.copy(file_data['path'], self.temp_file)
            elif file_id:
                import gdown
                # Blocking download call
                gdown.download(id=file_id, output=str(self.temp_file), quiet=True, fuzzy=True)

            # Only read simple text files in the thread. 
            # Docx/PDF content must be processed in the main thread (Finalize).
            if not file_name.endswith('.docx') and not file_name.endswith('.pdf'):
                with open(self.temp_file, 'r', encoding='utf-8', errors='ignore') as f:
                    self.thread_content = f.read()

        except Exception as e:
            self.thread_error = f"Failed to load content:\n\n{e}"
            logging.error(f"Doc load error: {e}")
        
    def _check_thread(self, file_data):
        """Polls the thread status and updates the GUI when complete."""
        if self.loading_thread.is_alive():
            self.after(100, self._check_thread, file_data)
        else:
            self._finalize_content_load(file_data)

    def _finalize_content_load(self, file_data):
        """Runs in the main thread to hide the loader and update the Text widget."""
        self._hide_loading()
        file_name = file_data['name'].lower()

        self.text_widget.config(state=tk.NORMAL) 
        
        try:
            if self.thread_error:
                self.text_widget.insert(tk.END, self.thread_error)
            elif self.thread_content:
                # Simple text file content
                self.text_widget.insert(tk.END, self.thread_content)
            elif self.temp_file and self.temp_file.exists():
                # DOCX or PDF file: Rendering must happen here in the main thread!
                if file_name.endswith('.docx'):
                    self.render_docx_perfect(str(self.temp_file))
                elif file_name.endswith('.pdf'):
                    self.render_pdf_perfect(str(self.temp_file))
            else:
                 self.text_widget.insert(tk.END, "Failed to locate file for rendering.")

        except Exception as e:
            self.text_widget.insert(tk.END, f"Failed to render document:\n\n{e}")
            logging.error(f"Doc render error: {e}")

        self.text_widget.config(state=tk.DISABLED) 

        # Final cleanup of the temporary file for successful loads
        if self.temp_file and self.temp_file.exists():
            try:
                self.temp_file.unlink()
            except:
                pass
    # ----------------------------------------
    
    def on_close(self):
        for img_path in self.temp_images:
            try:
                if Path(img_path).exists():
                    Path(img_path).unlink()
            except:
                pass
        self.destroy()

    def _open_link(self, url):
        webbrowser.open(url)

    # --- SCROLL FIX: Focus and Event Redirection (Preserved) ---
    def _on_scroll(self, event):
        """
        Forces the focus to the text widget and re-sends the event, 
        tricking the text widget into using its native proportional scroll handler.
        """
        if not self.text_widget.winfo_exists(): return
        
        # 1. Temporarily shift focus to the text widget.
        self.text_widget.focus_set()
        
        # 2. Re-generate the scroll event (Windows/Mac) on the text widget.
        if platform.system() == "Windows" or platform.system() == "Darwin":
            self.text_widget.event_generate("<MouseWheel>", delta=event.delta)

        # 3. Handle Linux (Button-4/5) explicitly
        elif event.num == 4: # Linux Up
            self.text_widget.yview_scroll(-3, "units")
        elif event.num == 5: # Linux Down
            self.text_widget.yview_scroll(3, "units")
                
        return "break"

    # --- DOCX RENDERER (Preserved) ---
    def render_docx_perfect(self, docx_path):
        try:
            doc = Document(docx_path)
        except Exception as e:
            self.text_widget.insert(tk.END, f"Error opening DOCX: {e}")
            return

        def iter_block_items(parent):
            if isinstance(parent, _Document):
                parent_elm = parent.element.body
            elif isinstance(parent, _Cell):
                parent_elm = parent._tc
            else:
                return
            for child in parent_elm.iterchildren():
                if 'CT_P' in globals() and isinstance(child, CT_P):
                    yield Paragraph(child, parent)
                elif 'CT_Tbl' in globals() and isinstance(child, CT_Tbl):
                    yield Table(child, parent)

        for block in iter_block_items(doc):
            if isinstance(block, Paragraph):
                self._render_paragraph(doc, block)
            elif isinstance(block, Table):
                self._render_table(block)

    def _render_paragraph(self, doc, paragraph):
        try:
            from docx.text.run import Run  
        except ImportError:
            return 

        style_name = paragraph.style.name.lower()
        tags = []
        prefix = ""

        if "heading" in style_name:
            tags.append("heading")
        if "list" in style_name:
            prefix = " • "

        if prefix:
            self.text_widget.insert(tk.END, prefix, tuple(tags))

        for element in paragraph._element.iterchildren():
            if element.tag == qn('w:r'): 
                run = Run(element, paragraph)
                
                try:
                    drawings = element.findall('.//' + qn('w:drawing'))
                    if drawings:
                        for drawing in drawings:
                            blips = drawing.findall('.//' + qn('a:blip'))
                            for blip in blips:
                                embed_id = blip.get(qn('r:embed'))
                                if embed_id:
                                    image_part = doc.part.related_parts.get(embed_id)
                                    if image_part:
                                        self._process_and_insert_image_blob(image_part.blob)
                                        self.text_widget.insert(tk.END, "\n")
                except Exception:
                    pass

                text = run.text
                if not text: continue

                run_tags = list(tags) 
                if run.bold: run_tags.append("bold")
                if run.italic: run_tags.append("italic")

                self.text_widget.insert(tk.END, text, tuple(run_tags))

            elif element.tag == qn('w:hyperlink'):
                r_id = element.get(qn('r:id'))
                if r_id and r_id in doc.part.rels:
                    rel = doc.part.rels[r_id]
                    url = rel.target_ref
                    link_text = ""
                    for run_element in element.findall(qn('w:r')):
                        t_element = run_element.find(qn('w:t'))
                        if t_element is not None and t_element.text:
                            link_text += t_element.text
                    
                    if link_text:
                        link_tag = f"link_{uuid.uuid4().hex}"
                        self.text_widget.tag_bind(link_tag, "<Button-1>", lambda e, u=url: self._open_link(u))
                        self.text_widget.insert(tk.END, link_text, ("link", link_tag))

        self.text_widget.insert(tk.END, "\n")
        if "heading" not in style_name:
             self.text_widget.insert(tk.END, "\n")


    def _render_table(self, table):
        table_frame = tk.Frame(self.text_widget, bg="#2c2c2c", pady=10)
        
        # Bind scroll events to the table frame itself
        table_frame.bind("<MouseWheel>", self._on_scroll)
        table_frame.bind("<Button-4>", self._on_scroll)
        table_frame.bind("<Button-5>", self._on_scroll)

        for i, row in enumerate(table.rows):
            for j, cell in enumerate(row.cells):
                cell_text = cell.text.strip()
                lbl = tk.Label(
                    table_frame, 
                    text=cell_text, 
                    bg="#2c2c2c", 
                    fg="#e0e0e0",
                    font=get_app_font(10),
                    borderwidth=1,
                    relief="solid",
                    padx=5, 
                    pady=5,
                    anchor="w",
                    justify=tk.LEFT
                )
                lbl.grid(row=i, column=j, sticky="nsew")
                
                # Bind scroll events to every cell label
                lbl.bind("<MouseWheel>", self._on_scroll)
                lbl.bind("<Button-4>", self._on_scroll)
                lbl.bind("<Button-5>", self._on_scroll)
            
        self.text_widget.window_create(tk.END, window=table_frame)
        self.text_widget.insert(tk.END, "\n\n")

    def _process_and_insert_image_blob(self, blob):
        try:
            img = Image.open(BytesIO(blob))
            tmp_path = Path(tempfile.gettempdir()) / f"docx_img_{uuid.uuid4().hex}.png"
            img.save(tmp_path)
            self.temp_images.append(tmp_path)
            self.insert_image(str(tmp_path)) 
        except Exception as e:
            logging.error(f"Failed to process image: {e}")

    # --- PDF RENDERER (Preserved) ---
    def render_pdf_perfect(self, pdf_path):
        import fitz
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            self.text_widget.insert(tk.END, f"Error opening PDF: {e}")
            return

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]
            blocks.sort(key=lambda b: b["bbox"][1])

            for block in blocks:
                if block["type"] == 0:
                    text = "\n".join(span["text"] for line in block["lines"] for span in line["spans"])
                    if text.strip():
                        self.text_widget.insert(tk.END, text + "\n\n")

            for img in page.get_images(full=True):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha < 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                
                tmp = Path(tempfile.gettempdir()) / f"pdf_{page_num}_{xref}.png"
                pix.save(str(tmp))
                self.temp_images.append(tmp)
                
                self.insert_image(str(tmp)) 
                self.text_widget.insert(tk.END, "\n")
                
                pix = None
            
            if page_num < len(doc) - 1:
                self.text_widget.insert(tk.END, "\n\n")
        doc.close()
        
    def insert_image(self, img_path):
        try:
            img = Image.open(img_path)
            max_width = 900
            
            if img.width > max_width:
                ratio = max_width / img.width
                new_h = int(img.height * ratio)
                img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
            
            photo = ImageTk.PhotoImage(img)
            self.image_refs.append(photo) 

            lbl = tk.Label(self.text_widget, image=photo, bg="#1e1e1e", bd=0)
            lbl.image = photo 
            
            # --- Bind scroll to image ---
            lbl.bind("<MouseWheel>", self._on_scroll)
            lbl.bind("<Button-4>", self._on_scroll)
            lbl.bind("<Button-5>", self._on_scroll)
            # ---------------------------------
            
            self.text_widget.window_create(tk.END, window=lbl)
            self.text_widget.insert(tk.END, "\n")
            
        except Exception as e:
            logging.warning(f"Image load error: {e}")

class ChangesDialog(tk.Toplevel):
    def __init__(self, parent, grouped_changes):
        super().__init__(parent)
        self.title("Latest Patch Changes")
        self.geometry("600x500")
        parent.center_window(self, 600, 500)
        self.transient(parent)
        self.grab_set()
        text_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, width=70, height=25, font=get_app_font(10))
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
        tk.Label(self, text=about_text, justify=tk.LEFT, font=get_app_font(10)).pack(pady=20)
        tk.Button(self, text="Open GitHub", command=lambda: webbrowser.open("https://github.com/d4rksp4rt4n/SteamGamePatcher")).pack(pady=5)
        tk.Button(self, text="Close", command=self.destroy).pack(pady=10)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Steam Game Patcher {APP_VERSION}")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = 1000
        height = 900
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(900, 600)
        self.resizable(True, True)
     
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
        self.patch_status_var = tk.StringVar(value="") # NEW
        # Menu bar
        menubar = tk.Menu(self)
        self.option_add('*tearOff', False)
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Latest Patch Changes...", command=lambda: ChangesDialog(self, self.grouped_changes))
        menubar.add_cascade(label="View", menu=view_menu)
      
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
        ETAG_PATH = DB_PATH.parent / 'patches_database.etag'
        def download_database():
            DB_PATH.parent.mkdir(exist_ok=True)
            headers = {}
            if DB_PATH.exists() and ETAG_PATH.exists():
                with ETAG_PATH.open('r') as f:
                    etag = f.read().strip()
                headers['If-None-Match'] = etag
            try:
                r = requests.get(DB_URL, headers=headers, timeout=15)
                logging.info(f"GitHub response: status={r.status_code}, headers={r.headers}")
                if r.status_code == 304:
                    logging.info("Database up to date (304)")
                    os.utime(DB_PATH)
                    return False
                r.raise_for_status()
                with DB_PATH.open('w', encoding='utf-8') as f:
                    f.write(r.text)
                new_etag = r.headers.get('ETag')
                if new_etag:
                    with ETAG_PATH.open('w') as f:
                        f.write(new_etag)
                else:
                    logging.warning("No ETag in response")
                os.utime(DB_PATH)
                logging.info("Database updated")
                return True
            except Exception as e:
                logging.error(f"Update failed: {e}")
                return False
        if not DB_PATH.exists():
            logging.info("Database file missing → forcing download")
            updated = download_database()
        else:
            age_seconds = time.time() - DB_PATH.stat().st_mtime
            if age_seconds > 3600:
                logging.info(f"Local database is {age_seconds:.0f}s old (>1h) → checking GitHub")
                updated = download_database()
            else:
                logging.info(f"Local database is fresh ({age_seconds:.0f}s old) → checking GitHub via ETag")
                updated = download_database()
        if not DB_PATH.exists():
            messagebox.showerror("No Database", "Download failed. Check internet.")
            sys.exit(1)
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            self.folder_db = json.load(f)
    
        metadata = self.folder_db.get('metadata', {})
        self.version = metadata.get('version', 'Unknown')
        recent_changes = metadata.get('recent_changes', [])
    
        db_status = "Updated" if updated else "Up to date"
        self.db_status = f"Database Version: {self.version} | Status: {db_status}"
    
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
        self.steam_path = steam
        # Build matches
        self.matches = []
        self.by_id = {}
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
        self.matches = sorted(self.matches, key=lambda x: x['game_name'].lower())
        logging.info(f"FOUND {len(self.matches)} matched games with patches")

        # LOAD LAST APPLIED FROM PER-GAME CONFIGS (MOVED AFTER installed + by_id)
        self.last_applied = self.load_per_game_configs()
        # Backward compat: Load old global if exists, migrate to per-game
        old_path = Path("data") / "last_applied.json"
        if old_path.exists():
            try:
                with open(old_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                self.migrate_old_to_per_game(old_data)
                old_path.unlink()  # Clean up old file
                logging.info("Migrated old global config to per-game configs")
            except Exception as e:
                logging.warning(f"Failed to migrate old config: {e}")

        self.build_gui()
        if self.matches:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.on_select(None)
        self.progress_frame = None
        self.ui_queue = queue.Queue()
        self.after(100, self.process_ui_queue)

    def load_per_game_configs(self):
        """Load last_applied from per-game patcher_config.json files."""
        last_applied = {}
        for appid, install_dir in self.installed.items():
            config_path = install_dir / CONFIG_FILENAME
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                    last_patch = config.get('last_patch', {})
                    if last_patch:
                        appid_str = str(appid)
                        if appid_str not in last_applied:
                            last_applied[appid_str] = {}
                        # Look up game_name from by_id
                        game_name = self.by_id.get(appid_str, {}).get('game_name', appid_str)  # Fallback to appid if no match
                        last_applied[appid_str][game_name] = last_patch
                        logging.debug(f"Loaded config for {appid}: {last_patch.get('file', 'N/A')}")
                except Exception as e:
                    logging.warning(f"Failed to load {config_path}: {e}")
        return last_applied

    def migrate_old_to_per_game(self, old_data):
        """Migrate old global JSON to per-game configs."""
        for appid_str, games in old_data.items():
            for game_name, patch_data in games.items():
                install_dir = self.installed.get(appid_str)
                if install_dir:
                    config_path = install_dir / CONFIG_FILENAME
                    try:
                        # Load existing or create new
                        if config_path.exists():
                            with open(config_path, 'r', encoding='utf-8') as f:
                                config = json.load(f)
                        else:
                            config = {}
                        
                        config['last_patch'] = patch_data
                        
                        with open(config_path, 'w', encoding='utf-8') as f:
                            json.dump(config, f, indent=4)
                    except Exception as e:
                        logging.warning(f"Failed to migrate {game_name}: {e}")

    def save_per_game_config(self, appid, game_name, file_name, date, changes):
        """Save last_patch + changes to game's patcher_config.json."""
        appid_str = str(appid)
        install_dir = self.installed.get(appid_str)
        if not install_dir:
            logging.error(f"No install dir for {appid}")
            return
        config_path = install_dir / CONFIG_FILENAME
        try:
            config = {}
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            config['last_patch'] = {
                'file': file_name,
                'date': date,
                'changes': changes  # {"overwritten": [...], "added": [...], "skipped": [...]}
            }
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            # Update in-memory for immediate UI refresh
            if appid_str not in self.last_applied:
                self.last_applied[appid_str] = {}
            self.last_applied[appid_str][game_name] = config['last_patch']
            logging.info(f"Saved config to {config_path}: {file_name} with {len(changes.get('overwritten', []))} overwrites")
        except Exception as e:
            logging.error(f"Failed to save {config_path}: {e}")

    def clear_cache(self):
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
        grouped = defaultdict(list)
        for change in changes:
            parts = change.split(" - ", 1)
            if len(parts) >= 2:
                game = parts[0]
                details = parts[1]
                grouped[game].append(details)
            else:
                grouped["Miscellaneous"].append(change)
        return dict(grouped)

    def process_ui_queue(self):
        try:
            while True:
                msg, args = self.ui_queue.get_nowait()
                if msg == "update_progress":
                    progress_var, value = args
                    if value == -1:
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
                elif msg == "save_per_game_config":
                    appid, game_name, file_name, date, changes = args
                    self.save_per_game_config(appid, game_name, file_name, date, changes)
        except queue.Empty:
            pass
        self.after(50, self.process_ui_queue)
       
    def refresh_after_patch(self):
        # Refresh treeview + re-select current game so ★ disappears instantly
        current_appid = self.current_appid
        self.last_applied = self.load_per_game_configs()  # Reload from files
        self.filter_games() # Rebuilds list with new last_applied data
        # Re-select the game that was just patched
        for item in self.tree.get_children():
            if self.tree.item(item)["tags"][0] == str(current_appid):
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.on_select(None)
                break

    def parse_size_bytes(self, size_str):
        import re
        if not size_str or str(size_str).strip().lower() == 'unknown':
            return None
        s = str(size_str).strip().replace(',', '')
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
        if s.isdigit():
            return int(s)
        return None

    def download_with_gdown(self, file_id, output_path, expected_bytes, progress_var, status_label, speed_label):
        output_path = Path(output_path)
        self.ui_queue.put(("update_status", (status_label, f"Downloading: {output_path.name}")))
        start_time = time.time()
        initial_size = output_path.stat().st_size if output_path.exists() else 0
        last_size = initial_size
        no_growth_count = 0
        max_no_growth = 10
        posix_path = output_path.as_posix()
     
        thread_error = []
        def run_gdown():
            try:
                import gdown
                gdown.download(id=file_id, output=posix_path, quiet=True, resume=True)
            except Exception as e:
                thread_error.append(e)
        download_thread = threading.Thread(target=run_gdown, daemon=True)
        download_thread.start()
        logging.debug(f"Started gdown thread for {output_path.name}")
        while download_thread.is_alive():
            if output_path.exists():
                current_size = output_path.stat().st_size
                if current_size > last_size:
                    last_size = current_size
                    no_growth_count = 0
                    if expected_bytes and expected_bytes > 0:
                        percent = min(100, (current_size / expected_bytes) * 100)
                        self.ui_queue.put(("update_progress", (progress_var, percent)))
                    else:
                        self.ui_queue.put(("update_progress", (progress_var, -1)))
                    elapsed = time.time() - start_time
                    if elapsed > 0.5:
                        speed_mb = (current_size - initial_size) / elapsed / (1024 * 1024)
                        self.ui_queue.put(("update_speed", (speed_label, f"{speed_mb:.2f} MB/s")))
                else:
                    no_growth_count += 1
            time.sleep(0.2)
        if thread_error:
            logging.error(f"gdown thread failed: {thread_error[0]}")
            raise RuntimeError(f"Download failed: {thread_error[0]}")
        actual_size = output_path.stat().st_size if output_path.exists() else 0
        if actual_size > initial_size:
            self.ui_queue.put(("update_progress", (progress_var, 100)))
            self.ui_queue.put(("update_speed", (speed_label, "Download complete")))
            self.ui_queue.put(("update_status", (status_label, f"Download Complete: {output_path.name}")))
            logging.info(f"Download completed: {actual_size} bytes")
        return actual_size

    def extract_with_7z(self, archive_path, extract_dir, progress_var=None):
        script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        local_7z = script_dir / '7z.exe'
        if not local_7z.exists():
            raise FileNotFoundError("7z.exe not found.")
        if not extract_dir.is_dir():
            extract_dir.mkdir(parents=True, exist_ok=True)
        if extract_dir.suffix == '.exe':
            extract_dir = extract_dir.with_suffix('')
            extract_dir.mkdir(exist_ok=True)
        cmd = [str(local_7z), 'x', str(archive_path), f'-o{extract_dir}', '-y', '-bsp1']
        no_window_flag = 0x08000000 if sys.platform == 'win32' else 0
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=no_window_flag
        )
        while True:
            chunk = process.stdout.read(64)
            if not chunk and process.poll() is not None:
                break
            if chunk:
                try:
                    text = chunk.decode('utf-8', errors='ignore')
                    matches = re.findall(r'\b(\d+)%', text)
                    if matches and progress_var:
                        percent = int(matches[-1])
                        self.ui_queue.put(("update_progress", (progress_var, percent)))
                except:
                    pass
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
        logging.info(f"Extracted with 7z: {archive_path}")

    def extract_archive(self, archive_path, extract_dir, progress_var=None):
        logging.debug(f"DEBUG: Archive path: {archive_path}")
        if not extract_dir.is_dir():
            extract_dir.mkdir(parents=True, exist_ok=True)
        if extract_dir.suffix == '.exe':
            extract_dir = extract_dir.with_suffix('')
            extract_dir.mkdir(exist_ok=True)
        ext = archive_path.suffix.lower()
        try:
            if ext == '.zip':
                if progress_var:
                    self.ui_queue.put(("update_progress", (progress_var, -1)))
                with zipfile.ZipFile(archive_path, 'r') as zf:
                    zf.extractall(extract_dir)
                    logging.info(f"Extracted ZIP: {len(zf.namelist())} files")
            else:
                self.extract_with_7z(archive_path, extract_dir, progress_var)
            if not os.listdir(extract_dir):
                logging.warning("Extraction produced no files—check archive.")
        except Exception as e:
            logging.error(f"Extraction failed: {e}")
            raise

    def smart_apply_patch(self, extract_dir, install_dir, status_label):
        game_files = defaultdict(list)
        for root, dirs, files in os.walk(install_dir):
            for file in files:
                game_files[file.lower()].append(os.path.join(root, file))
        overwritten_files = []
        added_files = []
        skipped_files = []
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
                        overwritten_files.append(str(relative))  # Track relative path
                        overwritten += 1
                        self.ui_queue.put(("update_status", (status_label, f"OVERWRITTEN: {file}")))
                    else:
                        skipped_files.append(str(relative))
                        skipped += 1
                        logging.warning(f"MULTIPLE MATCHES for {file}: {matches} - Skipping")
                        self.ui_queue.put(("update_status", (status_label, f"SKIPPED (multi-match): {file}")))
                else:
                    default_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, default_dst)
                    added_files.append(str(relative))
                    added += 1
                    self.ui_queue.put(("update_status", (status_label, f"ADDED: {file}")))
        changes = {
            "overwritten": overwritten_files,
            "added": added_files,
            "skipped": skipped_files if skipped_files else None  # Optional
        }
        return overwritten, added, skipped, changes

    def process_patch(self, files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label, appid):
        today_date = time.strftime("%Y-%m-%d")
        applied_file_name = None
        total_changes = None
        try:
            script_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
            local_7z = script_dir / '7z.exe'
            if not local_7z.exists():
                raise FileNotFoundError("7z.exe not found.")
            no_window_flag = 0x08000000 if sys.platform == 'win32' else 0
            for idx in selected_indices:
                f = files[idx]
                file_id = f['id']
                file_name = f['name']
                file_path = f.get('path', file_name)
                raw_size = f.get('size', 'Unknown')
                expected_bytes = self.parse_size_bytes(raw_size)
                if file_name.lower().endswith(('.txt', '.docx', '.pdf')):
                    self.ui_queue.put(("update_status", (status_label, f"Instructions viewed: {file_name}")))
                    continue
                cache_file = self.cache_dir / file_name
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                use_cache = False
                if cache_file.exists():
                    actual_size = os.path.getsize(cache_file)
                    small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                    tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                    test_cmd = [str(local_7z), 't', str(cache_file)]
                    test_result = subprocess.run(test_cmd, capture_output=True, text=True, creationflags=no_window_flag)
                    if test_result.returncode != 0:
                        logging.warning(f"Cached file failed integrity. Deleting.")
                        cache_file.unlink()
                    elif tolerance_check or small_file_check:
                        use_cache = True
                        logging.info(f"Using cached: {file_name}")
                output = cache_file
                if not use_cache:
                    retries = 0
                    max_retries = 3
                    while retries < max_retries:
                        logging.info(f"Downloading {file_path} (attempt {retries+1})")
                        self.ui_queue.put(("update_status", (status_label, f"Downloading: {file_path}")))
                        self.ui_queue.put(("update_progress", (progress_var, -1)))
                        self.download_with_gdown(file_id, output, expected_bytes or 0, progress_var, status_label, speed_label)
                        actual_size = os.path.getsize(output)
                        small_file_check = expected_bytes and expected_bytes < 2048 and actual_size > 0
                        tolerance_check = expected_bytes is None or (abs(actual_size - expected_bytes) <= expected_bytes * 0.05)
                        if tolerance_check or small_file_check:
                            test_cmd = [str(local_7z), 't', str(output)]
                            test_result = subprocess.run(test_cmd, capture_output=True, text=True, creationflags=no_window_flag)
                            if test_result.returncode == 0:
                                break
                        retries += 1
                        if output.exists():
                            output.unlink()
                    else:
                        raise ValueError(f"Download failed after {max_retries} attempts.")
                self.ui_queue.put(("update_status", (status_label, f"Extracting: {file_path}")))
                temp_extract_dir = Path(tempfile.mkdtemp())
                try:
                    if output.suffix.lower() == ".exe":
                        for flags in ['/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '/S', '']:
                            cmd = [str(output)] + flags.split()
                            result = subprocess.run(cmd, cwd=str(temp_extract_dir), capture_output=True, text=True, creationflags=no_window_flag)
                            if result.returncode == 0:
                                break
                        else:
                            raise RuntimeError("Self-extracting EXE failed")
                    else:
                        self.extract_archive(output, temp_extract_dir, progress_var)
                finally:
                    pass
                self.ui_queue.put(("update_status", (status_label, f"Applying: {file_path}")))
                overwritten, added, skipped, changes = self.smart_apply_patch(temp_extract_dir, install_dir, status_label)
                total_changes = changes  # Accumulate if multi-file, but for now per-file
                logging.info(f"Applied: {overwritten} overwritten, {added} added, {skipped} skipped")
                shutil.rmtree(temp_extract_dir, ignore_errors=True)
                if not file_name.lower().endswith(('.txt', '.docx', '.pdf')):
                    applied_file_name = file_name
            self.ui_queue.put(("update_status", (status_label, "SUCCESS")))
            if applied_file_name:
                self.ui_queue.put(("save_per_game_config", (appid, game_name, applied_file_name, today_date, total_changes or {})))
            self.after(100, lambda: messagebox.showinfo("SUCCESS", f"Patched:\n{game_name}\n\nApplied: {applied_file_name or 'files'}\nSaved config with changes."))
            self.after(600, self.refresh_after_patch)
        except Exception as e:
            error_msg = str(e)
            self.ui_queue.put(("update_status", (status_label, "FAILED")))
            logging.error(f"PATCH FAILED: {error_msg}")
            self.after(100, lambda: messagebox.showerror("PATCH FAILED", error_msg))
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
            messagebox.showerror("ERROR", "No patch data found.")
            return
        game_name = match["game_name"]
        install_dir = self.installed.get(appid)
        if not install_dir or not install_dir.exists():
            messagebox.showerror("ERROR", f"Game folder not found:\n{install_dir}")
            return
        files = match["data"].get("files", [])
        if not files:
            messagebox.showerror("ERROR", "No patch files defined for this game.")
            return            
        # --- NEW: Sort files alphabetically by name ---
        files.sort(key=lambda f: f['name'].lower())
        
        display_files = [f"{f.get('path', f['name'])} ({f.get('size', 'Unknown')})" for f in files]

        if not messagebox.askyesno("Apply Patch", f"Apply patch to:\n\n{game_name}\n\n{install_dir}\n\nContinue?"):
            return

        self.patch_btn.config(state="disabled", text="PREPARING...")
        self.status.config(text="Loading patch selection...", fg="orange")
        self.update_idletasks()

        # The dialog now receives the sorted list
        dialog = PatchSelectionDialog(self, display_files, files)
        self.wait_window(dialog)
        selected_indices = dialog.result
        if not selected_indices:
            self.reset_ui()
            return
        self.progress_frame = tk.Frame(self)
        self.progress_frame.pack(fill=tk.X, padx=15, pady=8)
        progress_var = tk.DoubleVar()
        self.progress_bar_widget = ttk.Progressbar(self.progress_frame, variable=progress_var, maximum=100, mode='indeterminate')
        self.progress_bar_widget.pack(fill=tk.X, pady=(0, 4))
        self.progress_bar_widget.start(10)
        status_label = tk.Label(self.progress_frame, text="Starting...", font=get_app_font(10))
        status_label.pack(anchor="w")
        speed_label = tk.Label(self.progress_frame, text="", font=get_app_font(9), fg="#00ff88")
        speed_label.pack(anchor="w")
        self.status.config(text="Downloading & applying patches...", fg="#3399ff")
        thread = threading.Thread(
            target=self.process_patch,
            args=(files, selected_indices, install_dir, game_name, progress_var, status_label, speed_label, appid),
            daemon=True
        )
        thread.start()

    def build_gui(self):
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        left_frame = tk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)
        self.img_label = tk.Label(left_frame, bg="#222", text="No Image", font=get_app_font(9))
        self.img_label.pack(pady=10)
        details_frame = tk.Frame(left_frame)
        details_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        def add_row(label_text, var, value_color="#ffffff"):
            row = tk.Frame(details_frame)
            row.pack(anchor="w", padx=12, pady=2)
            tk.Label(row, text=label_text, font=get_app_font(10, "bold"), fg="black").pack(side=tk.LEFT)
            label = tk.Label(row, textvariable=var, font=get_app_font(10), fg=value_color,
                             anchor="w", justify="left", wraplength=140)
            label.pack(side=tk.LEFT, fill=tk.X)
            return label
        add_row("Developer: ", self.dev_var, "black")
        add_row("Publisher: ", self.pub_var, "black")
        add_row("Notes: ", self.notes_var, "black")
        add_row("Status: ", self.status_var, "#4CAF50")
        patch_row = tk.Frame(details_frame)
        patch_row.pack(anchor="w", padx=12, pady=2)
        self.patch_status_label = tk.Label(patch_row, textvariable=self.patch_status_var,
                                          font=get_app_font(10), fg="#4CAF50",
                                          anchor="w", justify="left", wraplength=220)
        self.patch_status_label.pack(fill=tk.X)
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
        self.open_gdrive_btn = tk.Button(buttons_frame, text="Open Google Drive Folder",
                                         command=self.open_gdrive_folder, state=tk.DISABLED,
                                         font=get_app_font(10), bg="#1a1a1a", fg="#cccccc")
        self.open_gdrive_btn.pack(fill=tk.X, padx=12, pady=4)
        self.launch_btn = tk.Button(buttons_frame, text="Launch Game",
                                    command=self.launch_game, state=tk.DISABLED,
                                    font=get_app_font(10), bg="#333333", fg="#cccccc")
        self.launch_btn.pack(fill=tk.X, padx=12, pady=(4, 8))
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
        # UPDATE PRIORITY + ★ MARKER
        games_with_update = []
        games_without_update = []
        for match in self.matches:
            appid_str = str(match["data"]["appid"])
            game_name = match["game_name"]
            local_data = self.last_applied.get(appid_str, {}).get(game_name, {})
            local_file = local_data.get("file")
            update_available = False
            if local_file:
                file_still_exists = any(local_file == f["name"] for f in match["data"]["files"])
                update_available = not file_still_exists
            if update_available:
                games_with_update.append(match)
            else:
                games_without_update.append(match)
        games_with_update = sorted(games_with_update, key=lambda m: m["game_name"].lower())
        games_without_update = sorted(games_without_update, key=lambda m: m["game_name"].lower())
        display_matches = games_with_update + games_without_update
        for match in display_matches:
            appid = str(match["data"]["appid"])
            game_name = match["game_name"]
            local_data = self.last_applied.get(appid, {}).get(game_name, {})
            local_file = local_data.get("file")
            update_available = False
            if local_file:
                file_still_exists = any(local_file == f["name"] for f in match["data"]["files"])
                update_available = not file_still_exists
            if update_available:
                display_name = f"★ {game_name}"
                tags = (appid, "update")
            else:
                display_name = game_name
                tags = (appid,)
            self.tree.insert("", "end", values=(display_name,), tags=tags)
        self.tree.tag_configure("update", foreground="#e67e22", font=get_app_font(11, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        bottom_frame = tk.Frame(self, bg="#1e1e1e")
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
        self.status = tk.Label(bottom_frame, text=self.db_status, anchor="w",
                               font=get_app_font(10), bg="#1e1e1e", fg="#00ff88", padx=12)
        self.status.pack(fill=tk.X, side=tk.LEFT, expand=True)

    def filter_games(self, event=None):
        search_term = self.search_var.get().lower().strip()
        for item in self.tree.get_children():
            self.tree.delete(item)
        filtered = [m for m in self.matches if search_term in m['game_name'].lower()]
        games_with_update = []
        games_without_update = []
        for match in filtered:
            appid_str = str(match["data"]["appid"])
            game_name = match["game_name"]
            local_data = self.last_applied.get(appid_str, {}).get(game_name, {})
            local_file = local_data.get("file")
            update_available = False
            if local_file:
                file_still_exists = any(local_file == f["name"] for f in match["data"]["files"])
                update_available = not file_still_exists
            if update_available:
                games_with_update.append(match)
            else:
                games_without_update.append(match)
        games_with_update = sorted(games_with_update, key=lambda m: m["game_name"].lower())
        games_without_update = sorted(games_without_update, key=lambda m: m["game_name"].lower())
        display_matches = games_with_update + games_without_update
        for match in display_matches:
            appid = str(match["data"]["appid"])
            game_name = match["game_name"]
            local_data = self.last_applied.get(appid, {}).get(game_name, {})
            local_file = local_data.get("file")
            update_available = False
            if local_file:
                file_still_exists = any(local_file == f["name"] for f in match["data"]["files"])
                update_available = not file_still_exists
            if update_available:
                display_name = f"★ {game_name}"
                tags = (appid, "update")
            else:
                display_name = game_name
                tags = (appid,)
            self.tree.insert("", "end", values=(display_name,), tags=tags)
        # THIS LINE IS REQUIRED IN FILTER_GAMES TOO!
        self.tree.tag_configure("update", foreground="#e67e22", font=get_app_font(11, "bold"))
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
        game_name = match["game_name"] # ← CRITICAL: define game_name
        img = load_box_art(self.steam_path, appid)
        if img:
            self.img_label.configure(image=img, text="")
            self.img_label.image = img
        else:
            self.img_label.configure(image="", text="No box art")
        self.dev_var.set(match['dev_name'])
        self.pub_var.set(match['data'].get('publisher', 'N/A'))
        self.notes_var.set(match['data'].get('notes', 'N/A'))
                # === SIMPLE & PERFECT UPDATE DETECTION ===
        local_data = self.last_applied.get(appid, {}).get(game_name, {})
        local_file = local_data.get("file")
        changes = local_data.get("changes", {})
        update_available = False
        if local_file:
            # If the file the user applied no longer exists in the current database → it was replaced → UPDATE!
            file_exists = any(local_file == f["name"] for f in match["data"]["files"])
            update_available = not file_exists
        else:
            # First time seeing this game → show as available, not update
            update_available = False
        if update_available:
            patch_text = "UPDATE AVAILABLE\nA new patch has been released!"
            fg = "#e67e22"
        elif local_file:
            ow = len(changes.get("overwritten", []))
            ad = len(changes.get("added", []))
            sk = len(changes.get("skipped", [])) if changes.get("skipped") else 0
            change_summary = f"{ow} overwritten, {ad} added"
            if sk > 0:
                change_summary += f", {sk} skipped"
            patch_text = f"Latest applied:\n{local_file}\non {local_data.get('date', 'unknown')}\n\n{change_summary}"
            fg = "#4CAF50"
        else:
            patch_text = "Patch available"
            fg = "#3498db"
        self.patch_status_var.set(patch_text)
        self.patch_status_label.config(fg=fg)
        self.patch_status_label.config(wraplength=220)
        self.status_var.set(match['data'].get('store_status', 'N/A'))
        self.current_appid = appid
        self.current_install_dir = self.installed[appid]
        self.open_folder_btn.config(state=tk.NORMAL)
        self.open_gdrive_btn.config(state=tk.NORMAL)
        self.launch_btn.config(state=tk.NORMAL)

    def clear_details(self):
        self.img_label.configure(image="", text="No Image")
        self.dev_var.set("")
        self.pub_var.set("")
        self.notes_var.set("")
        self.status_var.set("")
        self.patch_status_var.set("")
        self.open_folder_btn.config(state=tk.DISABLED)
        self.open_gdrive_btn.config(state=tk.DISABLED)
        self.launch_btn.config(state=tk.DISABLED)
        self.current_appid = None
        self.current_install_dir = None

    def open_folder(self):
        if self.current_install_dir and self.current_install_dir.exists():
            os.startfile(str(self.current_install_dir))
        else:
            messagebox.showerror("Error", "Game folder not found")
           
    def open_gdrive_folder(self):
        if not self.current_appid:
            return
        match = self.by_id.get(self.current_appid)
        if not match:
            return
       
        game_data = match["data"]
        game_id = game_data.get("id") # This is the Google Drive folder ID for the game
       
        if game_id:
            url = f"https://drive.google.com/drive/folders/{game_id}"
            webbrowser.open(url)
        else:
            messagebox.showwarning("No Link", "Google Drive folder ID not found for this game.")

    def launch_game(self):
        if self.current_appid:
            url = f"steam://run/{self.current_appid}"
            os.startfile(url)
        else:
            messagebox.showerror("Error", "No game selected")

    def reset_ui(self):
        try:
            if hasattr(self, 'patch_btn') and self.patch_btn.winfo_exists():
                self.patch_btn.config(state="normal", text="Patch Selected Game")
            if hasattr(self, 'status') and self.status.winfo_exists():
                self.status.config(text=self.db_status, fg="#00ff88")
            if hasattr(self, 'progress_frame') and self.progress_frame and self.progress_frame.winfo_exists():
                self.progress_frame.destroy()
                self.progress_frame = None
            if hasattr(self, "patch_status_var"):
                self.patch_status_var.set("")
        except:
            pass

    def center_window(self, window, width=None, height=None):
        window.update_idletasks()
        main_x = self.winfo_rootx()
        main_y = self.winfo_rooty()
        main_w = self.winfo_width()
        main_h = self.winfo_height()
        win_w = width or window.winfo_width()
        win_h = height or window.winfo_height()
        x = main_x + (main_w - win_w) // 2
        y = main_y + (main_h - win_h) // 2
        x = max(0, x)
        y = max(0, y)
        window.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def get_main_app(self):
        return self

if __name__ == "__main__":
    setup_logging()
    ensure_7z_exe()
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
    # Add support for DOCX and PDF
    try:
        from docx import Document
    except:
        subprocess.call([sys.executable, "-m", "pip", "install", "python-docx"])
        from docx import Document
    try:
        import fitz  # PyMuPDF for PDF
    except:
        subprocess.call([sys.executable, "-m", "pip", "install", "pymupdf"])
        import fitz
    App().mainloop()