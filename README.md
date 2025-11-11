# Steam Game Patcher

[![GitHub Repo stars](https://img.shields.io/github/stars/d4rksp4rt4n/SteamGamePatcher?style=social)](https://github.com/d4rksp4rt4n/SteamGamePatcher)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)

A user-friendly desktop application for applying community patches to Steam games. Automatically detects installed games, loads box art, and downloads/extracts patches from a centralized database (hosted on this repo). Supports ZIP, 7Z, RAR, and self-extracting EXE archives.

**Warning**: This tool modifies game files. Always back up your Steam installs ou recheck installed through Steam to revert changes. Use at your own risk — patches are community-sourced and may violate game ToS.

## Features
- **Auto-Detection**: Scans your Steam libraries for installed games with available patches.
- **Rich UI**: Tkinter-based interface with game box art, developer/publisher info, notes, and search/filtering.
- **Patch Management**: Select and apply specific patches via Google Drive links (gdown). Handles multi-file archives.
- **Smart Extraction**: Uses bundled 7z.exe for robust support (ZIP, 7Z, RAR, EXE).
- **Game Actions**: Open game folder or launch via Steam directly from the app.
- **Live Database**: Auto-updates patch info from GitHub JSON (with recent changes ticker).
- **Cross-Platform**: Windows-focused (uses `os.startfile`), but Python core works on macOS/Linux with tweaks.

## Requirements
- **Python 3.8+** (tested on 3.12)
- **Steam** installed (app detects path automatically).
- **Dependencies** (auto-installed on first run; see `requirements.txt` for full list):
  - App: `requests`, `Pillow` (PIL), `gdown`, `vdf`.
  - Build Scripts: `google-api-python-client`, `google-auth`, `pywin32` (optional).
- **7z.exe**: Bundled in the release (LGPL-2.1 licensed; see below).

Admin rights might be needed for auto-patchers executables.

## Installation
### From Source (Recommended for Dev)
1. Clone the repo:

   git clone https://github.com/d4rksp4rt4n/SteamGamePatcher.git
   cd SteamGamePatcher

