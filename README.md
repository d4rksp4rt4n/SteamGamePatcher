# Steam Game Patcher

[![GitHub Repo stars](https://img.shields.io/github/stars/d4rksp4rt4n/SteamGamePatcher?style=social)](https://github.com/d4rksp4rt4n/SteamGamePatcher)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)

A **fast, smart, and user-friendly** desktop tool for applying R18/adult patches to Steam games.

Automatically detects your installed games, shows beautiful box art, downloads patches from our Google Drive, extracts them safely, and applies them intelligently (not all patches might work with auto-apply function yet).

**Warning**: This tool modifies game files. Always backup or use Steam's "Verify Integrity" to revert. Use at your own risk.

## Features

- **Smart Auto-Detection** – Scans all Steam libraries instantly
- **Functional UI** – Full box art, developer/publisher info, patch notes, search & filter
- **Intelligent Patching** – Overwrites only changed files, adds new ones, skips duplicates
- **Robust Extraction** – Bundled `7z.exe` supports ZIP, 7Z, RAR, and self-extracting EXEs
- **Live Database** – Auto-updates from GitHub using **ETag caching**
- **Real-time Progress** – Uses threading and a smooth progress bar to prevent freezing during patch downloads and document processing.
- **Game Actions** – Launch via Steam or open install folder directly
- **Recent Changes Log** – See what’s new in the latest patches (Menu → View)
- **Cache System** – Downloaded patches are cached locally (Tools → Clear Cache to free space)
- **Advanced Instructions Viewer** – Supports internal rendering documents (DOCX, PDF, and TXT).

## Smart Database Updates

The app now uses **conditional requests with ETag** to check for database updates:

- Checks GitHub on every launch
- If nothing changed → **304 Not Modified** (tiny request, 0 KB download)
- Only downloads the full JSON when there’s an actual update
- You’ll see it in the log: `Database up to date (304)`

## Requirements

- **Windows** (primary target; macOS/Linux possible with minor tweaks)
- **Steam** installed (auto-detected)
- **Python 3.8+** (for source) or just the standalone `.exe`
- Internet connection (for database + patches)

Dependencies auto-installed on first run: 
`prequests`, `Pillow`, `gdown`, `vdf`, `python-docx`, `PyMuPDF` (for PDF rendering)

Bundled: `7z.exe` + `7z.dll` (LGPL-2.1)

## Installation

### Option 1: Standalone EXE (Recommended for most users)
Download the latest release from:
https://github.com/d4rksp4rt4n/SteamGamePatcher/releases

Just run `SteamGamePatcher.exe` — no Python needed!

### Option 2: From Source (Developers / Latest Features)

  git clone https://github.com/d4rksp4rt4n/SteamGamePatcher.git
  
  cd SteamGamePatcher
  
  pip install -r requirements.txt
  
  python SteamGamePatcher.py

Usage

1. Launch the app
2. Your patchable games appear instantly (with box art!)
3. Search or browse → click a game
4. Click Patch Selected Game
5. Choose which patch files to apply
6. Watch it download, extract, and patch automatically
7. Done! Launch the game from the app or Steam

Tip: 
  Use Menu → Tools → Clear Cache to free up disk space (patches stay cached by default).
      
  No admin rights needed, but auto-patchers executables might require OS admin rights on Windows to run.

## Database
- Patches defined in `database/data/patches_database.json` (JSON format: developers > games > appid/files).
- Auto-downloads/updates from this repo.

## Bundled 7z.exe
- **Source**: Official 7-Zip binary (https://www.7-zip.org/).
- **License**: LGPL-2.1. You may replace it with your own build.
- **Compliance**: Full source available at 7-zip.org. This app dynamically calls it—no static linking.

## Building/Development
pip install pyinstaller
pyinstaller --onefile --windowed --icon=icon.ico --add-data "7z.exe;." --add-data "7z.dll;." --add-data "no-box-art.png;." --add-data "Roboto-Regular.ttf;." SteamGamePatcher.py

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
Bundled 7-Zip (7z.exe/7z.dll): LGPL-2.1 (source: https://www.7-zip.org)
Box art: Loaded from Steam (non-commercial fair use)
Patches: Provided by community — use responsibly

## Issues & Support
- Report bugs/feature requests: [Issues](https://github.com/d4rksp4rt4n/SteamGamePatcher/issues).

## Acknowledgments
- Thanks to 7-Zip team for the extractor.
- Steam – for box art and library detection.

---

*Last updated: December 2025*

