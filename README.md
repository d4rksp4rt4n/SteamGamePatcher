# Steam Game Patcher

[![GitHub Repo stars](https://img.shields.io/github/stars/d4rksp4rt4n/SteamGamePatcher?style=social)](https://github.com/d4rksp4rt4n/SteamGamePatcher)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)

A user-friendly desktop application for applying community patches to Steam games. Automatically detects installed games, loads box art, and downloads/extracts patches from a centralized database (hosted on this repo). Supports ZIP, 7Z, RAR, and self-extracting EXE archives.

**Warning**: This tool modifies game files. Always back up your Steam installs ou recheck files through Steam to revert changes. Use at your own risk—patches are community-sourced and may violate game ToS.

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
- **Dependencies** (auto-installed on first run):
  - `requests`, `Pillow` (PIL), `gdown`, `vdf` (via pip).
- **7z.exe**: Bundled in the release (LGPL-2.1 licensed; see below).

No admin rights needed, but auto-patchers executable run through the app might require OS admin rights.

## Installation
### From Source (Recommended for Dev)
1. Clone the repo:

   git clone https://github.com/d4rksp4rt4n/SteamGamePatcher.git
   cd SteamGamePatcher

2. Install dependencies:

   pip install -r requirements.txt

*(Create `requirements.txt` with: `requests pillow gdown vdf`)*
3. Run:
  python main.py

- On first run, it auto-downloads the patch database and installs missing deps.

## Usage
1. **Launch the App**: Run `python main.py` or the EXE.
2. **Browse Games**: The list auto-populates with patchable installed games (sorted alphabetically). Use the search bar to filter.
3. **Select a Game**: Click to load details, box art, and patch info.
4. **Apply Patches**:
- Click **Patch Selected Game**.
- Confirm the target folder.
- Choose files in the dialog (shows sizes).
- Watch progress (indeterminate for downloads, console for speeds).
5. **Other Actions**:
- **Open Game Folder**: Explores the install dir.
- **Launch Game**: Starts via Steam URL.
6. **View Changes**: Menu > View > Latest Patch Changes (cycles recent updates).

**Pro Tip**: Patches are applied "smartly"—overwrites existing files, adds new ones, skips conflicts. Always verify after patching.

## Database
- Patches defined in `database/data/patches_database.json` (JSON format: developers > games > appid/files).
- Auto-downloads/updates from this repo.
- Contribute: Edit JSON, test patches, submit PRs. Include appid, file IDs (Google Drive), sizes, notes.

## Bundled 7z.exe
- **Source**: Official 7-Zip binary (https://www.7-zip.org/).
- **License**: LGPL-2.1. You may replace it with your own build.
- **Compliance**: Full source available at 7-zip.org. This app dynamically calls it—no static linking.

## Building/Development
- **Freeze to EXE**: Use PyInstaller (see above). Ensure `7z.exe` is in the bundle.
- **Testing**: Mock Steam paths with env vars. Run with `DEBUG=1` for logs.
- **Extending**: Add games to JSON. For new archive types, extend `process_patch()`.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

**Bundled Components**:
- 7z.exe: LGPL-2.1 (7-zip.org).
- Dependencies: See `requirements.txt` (mostly MIT/Apache).

## Contributing
Pull requests welcome! For major changes, open an issue first.

1. Fork the repo.
2. Create a feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit changes (`git commit -m 'Add some AmazingFeature'`).
4. Push (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

## Issues & Support
- Report bugs/feature requests: [Issues](https://github.com/d4rksp4rt4n/SteamGamePatcher/issues).

## Acknowledgments
- Inspired by community modding tools.
- Thanks to 7-Zip team for the extractor.
- Icons/Box Art: Steam API (non-commercial use).

---

*Last updated: November 2025*

