"""
Patch folder indexing script to sync Google Drive folders with local JSON, including file lists in each game folder.
Version: 1.8.5
Changes:
- v1.4.0: Fixed sync issues and restored detailed logging.
- v1.5.0: **CRITICAL FIX**: Changed logging level from INFO to DEBUG to show detailed file-by-file processing logs during scans.
- v1.6.0: Added .pdf and .docx to supported file types for instruction viewing in the app.
- v1.7.0: Fixed script not indexing new game folders from new developer folders.
- v1.7.1: Fixed script not indexing new game folders from existing developer folders.
- v1.7.2: Fixed early skip for trivial changes.
- v1.8.0: **MAJOR METADATA IMPROVEMENT**: 
  * recent_changes now excludes all readme files (.pdf, .docx, .txt)
  * Only real patch files (.zip, .7z, .rar, .exe) are logged as "📦 UPDATED PATCH"
  * Keeps exactly the last 10 changes (newest on top)
  * Added clean emojis and consistent formatting for all events (➕ DEVELOPER, ➕ GAME, 🗑 REMOVED, ✏ RENAMED, 🔄 FULL RESCAN)
  * Improved file change detection to eliminate "fake" update logs
- v1.8.5: **TRUE LEGACY FIX** - No more mass updates on old files
"""

import os
import json
import sys
import time
import logging
import datetime
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

try:
    import win32file
    import pywintypes
except ImportError:
    win32file = None

SERVICE_ACCOUNT_FILE = 'service-account.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CHANGE_TOKEN_FILE = 'database/data/change_token.txt'
ROOT_FOLDER_ID = '1a7jIAJoELzB3HpXNcuF0tGmDq1jqHs9-'
OUTPUT_JSON = 'database/data/patches_database.json'

MAX_RETRIES = 3
RETRY_DELAY_BASE = 1
IMPORTANT_PATCH_EXTS = {'.zip', '.7z', '.rar', '.exe'}
NON_PATCH_EXTS = {'.txt', '.pdf', '.docx', '.doc', '.rtf'}

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def is_file_locked(file_path):
    if not win32file:
        return False
    try:
        handle = win32file.CreateFile(file_path, win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None)
        win32file.CloseHandle(handle)
        return False
    except pywintypes.error:
        return True

def is_valid_json(file_path):
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except Exception:
        return False

def authenticate_drive():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logger.error(f"Missing {SERVICE_ACCOUNT_FILE}")
        sys.exit(1)
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        logger.info("Drive service authenticated successfully")
        return service
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)

def execute_with_retries(request, operation_name):
    for attempt in range(MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in [429, 500] and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                continue
            raise
    raise Exception(f"Failed {operation_name} after {MAX_RETRIES} retries")

def list_files(drive_service, folder_id, folders_only=False):
    results = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"
    if folders_only:
        query += " and mimeType='application/vnd.google-apps.folder'"
    while True:
        response = execute_with_retries(
            drive_service.files().list(q=query, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)", pageSize=100, pageToken=page_token),
            "list_files"
        )
        results.extend(response.get('files', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return results

def recursive_list_files_with_path(drive_service, folder_id, current_path='', ignore_folder_names=['Old']):
    results = []
    items = list_files(drive_service, folder_id)
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            if item['name'] in ignore_folder_names:
                continue
            sub_path = f"{current_path}{item['name']}/" if current_path else f"{item['name']}/"
            results.extend(recursive_list_files_with_path(drive_service, item['id'], sub_path))
        else:
            ext = Path(item['name']).suffix.lower()
            if ext in IMPORTANT_PATCH_EXTS | NON_PATCH_EXTS:
                raw_size = int(item.get('size', 0))
                size_str = f"{raw_size / 1024 / 1024:.1f} MB" if raw_size > 1024 * 1024 else f"{raw_size / 1024:.1f} KB"
                file_path = f"{current_path}{item['name']}" if current_path else item['name']
                results.append({
                    'name': item['name'],
                    'id': item['id'],
                    'size': size_str,
                    'raw_size': raw_size,
                    'type': ext,
                    'path': file_path,
                    'modifiedTime': item.get('modifiedTime')
                })
    return results

def find_game_and_path(drive_service, start_parent_id, game_id_to_path):
    path_parts = []
    current_id = start_parent_id
    steps = 0
    while current_id and current_id not in game_id_to_path and steps < 20:
        steps += 1
        try:
            folder_info = execute_with_retries(drive_service.files().get(fileId=current_id, fields='name,parents'), "get folder info")
            if folder_info['name'] == 'Old':
                return None
            parents = folder_info.get('parents', [])
            if not parents:
                return None
            path_parts.append(folder_info['name'])
            current_id = parents[0]
        except HttpError:
            return None
    if current_id not in game_id_to_path:
        return None
    return path_parts[::-1], current_id

def get_changes(drive_service, change_token):
    changes = []
    while True:
        response = execute_with_retries(
            drive_service.changes().list(pageToken=change_token, spaces='drive', fields='nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,parents,trashed,size,modifiedTime))'),
            "get_changes"
        )
        changes.extend(response.get('changes', []))
        change_token = response.get('nextPageToken') or response.get('newStartPageToken')
        if not response.get('nextPageToken'):
            break
    new_token = response.get('newStartPageToken') or drive_service.changes().getStartPageToken().execute().get('startPageToken')
    return changes, new_token

def load_change_token():
    if os.path.exists(CHANGE_TOKEN_FILE):
        with open(CHANGE_TOKEN_FILE, 'r') as f:
            token = f.read().strip()
        if token:
            logger.info(f"Loaded change token: {token[:10]}...")
            return token
    return None

def save_change_token(token):
    os.makedirs(os.path.dirname(CHANGE_TOKEN_FILE), exist_ok=True)
    with open(CHANGE_TOKEN_FILE, 'w') as f:
        f.write(token)

def load_last_folders():
    if not is_valid_json(OUTPUT_JSON):
        return {"developers": {}, "metadata": {}}
    with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    logger.info(f"Loaded {len(data.get('developers', {}))} developers")
    return data

def save_database(folder_structure):
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    temp_file = OUTPUT_JSON + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(folder_structure, f, indent=4, ensure_ascii=False)
    if os.path.exists(OUTPUT_JSON):
        os.remove(OUTPUT_JSON)
    os.rename(temp_file, OUTPUT_JSON)
    logger.debug(f"Database saved → {OUTPUT_JSON}")

def build_id_maps(folder_structure):
    dev_id_to_name = {}
    game_id_to_path = {}
    file_id_to_game = {}
    for dev_name, dev in folder_structure.get('developers', {}).items():
        dev_id_to_name[dev['id']] = dev_name
        for game_name, game in dev['games'].items():
            game_id_to_path[game['id']] = (dev_name, game_name)
            for f in game.get('files', []):
                file_id_to_game[f['id']] = (dev_name, game_name)
    return dev_id_to_name, game_id_to_path, file_id_to_game

def handle_deletion(id_, folder_structure, dev_id_to_name, game_id_to_path, file_id_to_game, new_changes):
    # (unchanged deletion logic - same as before)
    if id_ in dev_id_to_name:
        name = dev_id_to_name[id_]
        logger.info(f"Removing developer: {name}")
        new_changes.append(f"🗑 REMOVED DEVELOPER: {name}")
        del folder_structure['developers'][name]
        return True
    elif id_ in game_id_to_path:
        dev, name = game_id_to_path[id_]
        logger.info(f"Removing game: {name} from {dev}")
        new_changes.append(f"🗑 REMOVED GAME: {dev}/{name}")
        del folder_structure['developers'][dev]['games'][name]
        return True
    elif id_ in file_id_to_game:
        dev, name = file_id_to_game[id_]
        files = folder_structure['developers'][dev]['games'][name]['files']
        removed = [f['name'] for f in files if f['id'] == id_]
        folder_structure['developers'][dev]['games'][name]['files'] = [f for f in files if f['id'] != id_]
        for r in removed:
            logger.info(f"Removing file: {r}")
            new_changes.append(f"🗑 REMOVED FILE: {dev}/{name}/{r}")
        return True
    return False

def index_game_folders(root_folder_id, drive_service, last_folders, use_changes=False, change_token=None):
    logger.info(f"Starting indexing from root {root_folder_id}")
    folder_structure = last_folders.copy()
    new_change_token = None
    change_count = 0
    new_changes = []

    if use_changes:
        logger.info("Using incremental mode")
        try:
            changes, new_change_token = get_changes(drive_service, change_token)
            dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)

            for change in changes:
                file_id = change.get('fileId')
                is_removed = change.get('removed', False)
                file = change.get('file')

                if is_removed or (file and file.get('trashed')):
                    if handle_deletion(file_id, folder_structure, dev_id_to_name, game_id_to_path, file_id_to_game, new_changes):
                        change_count += 1
                        dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                    continue

                if not file:
                    continue

                id_ = file['id']
                name = file.get('name', '')
                mime = file['mimeType']
                parents = file.get('parents', [])
                parent = parents[0] if parents else None
                ext = Path(name).suffix.lower()

                if mime == 'application/vnd.google-apps.folder':
                    # (folder logic unchanged - same as 1.8.4)
                    if id_ in dev_id_to_name:
                        old_name = dev_id_to_name[id_]
                        if parent != root_folder_id:
                            handle_deletion(id_, folder_structure, dev_id_to_name, game_id_to_path, file_id_to_game, new_changes)
                        elif old_name != name:
                            folder_structure['developers'][name] = folder_structure['developers'].pop(old_name)
                            folder_structure['developers'][name]['id'] = id_
                            new_changes.append(f"✏ RENAMED DEVELOPER: {old_name} -> {name}")
                            change_count += 1
                    elif id_ in game_id_to_path:
                        old_dev, old_name = game_id_to_path[id_]
                        if parent is None or parent not in dev_id_to_name:
                            handle_deletion(id_, folder_structure, dev_id_to_name, game_id_to_path, file_id_to_game, new_changes)
                        else:
                            new_dev = dev_id_to_name[parent]
                            if old_dev != new_dev or old_name != name:
                                game_data = folder_structure['developers'][old_dev]['games'].pop(old_name)
                                folder_structure['developers'][new_dev]['games'][name] = game_data
                                game_data['id'] = id_
                                new_changes.append(f"✏ MOVED/RENAMED GAME: {old_dev}/{old_name} -> {new_dev}/{name}")
                                change_count += 1
                    else:
                        if parent == root_folder_id:
                            logger.info(f"Added developer: {name}")
                            new_changes.append(f"➕ DEVELOPER: {name}")
                            folder_structure['developers'][name] = {'id': id_, 'games': {}}
                            change_count += 1
                            game_folders = list_files(drive_service, id_, folders_only=True)
                            for gf in game_folders:
                                gname = gf['name']
                                gid = gf['id']
                                new_changes.append(f"➕ GAME (new dev): {name}/{gname}")
                                gfiles = recursive_list_files_with_path(drive_service, gid)
                                folder_structure['developers'][name]['games'][gname] = {"id": gid, "files": gfiles}
                                change_count += 1
                        elif parent in dev_id_to_name:
                            dev_name = dev_id_to_name[parent]
                            logger.info(f"Added game: {name} to {dev_name}")
                            new_changes.append(f"➕ GAME: {dev_name}/{name}")
                            game_files = recursive_list_files_with_path(drive_service, id_)
                            folder_structure['developers'][dev_name]['games'][name] = {"id": id_, "files": game_files}
                            change_count += 1
                    dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                    continue

                # === FILE LOGIC v1.8.5 ===
                if ext in NON_PATCH_EXTS:
                    logger.debug(f"Skipping non-patch file (doc/install note): {name}")
                    continue
                if ext not in IMPORTANT_PATCH_EXTS:
                    continue

                if id_ in file_id_to_game:
                    old_dev, old_game = file_id_to_game[id_]
                    old_files = folder_structure['developers'][old_dev]['games'][old_game]['files']
                    existing_file = next((f for f in old_files if f['id'] == id_), None)

                    if existing_file:
                        new_raw_size = int(file.get('size', 0))
                        new_size_str = f"{new_raw_size / 1024 / 1024:.1f} MB" if new_raw_size > 1024 * 1024 else f"{new_raw_size / 1024:.1f} KB"
                        parent_changed = bool(parents) and parents[0] != folder_structure['developers'][old_dev]['games'][old_game]['id']
                        name_changed = name != existing_file['name']

                        new_time = file.get('modifiedTime')
                        old_time = existing_file.get('modifiedTime')

                        if old_time is None:
                            # === TRUE LEGACY CHECK v1.8.5 ===
                            if 'raw_size' not in existing_file:
                                # Pre-raw_size file → ignore size completely
                                time_changed = name_changed or parent_changed
                            else:
                                old_raw_size = existing_file.get('raw_size', 0)
                                raw_size_changed = new_raw_size != old_raw_size
                                time_changed = name_changed or raw_size_changed or parent_changed

                            if not time_changed:
                                logger.debug(f"No meaningful change for legacy patch file {name} - skipping")
                                continue
                            logger.debug(f"Real change detected for legacy patch {name}")
                        else:
                            time_changed = new_time and new_time > old_time
                            if not time_changed:
                                logger.debug(f"Modified time not newer for {name} - skipping (API noise)")
                                continue
                            if not (name_changed or (new_size_str != existing_file.get('size')) or parent_changed):
                                logger.debug(f"No meaningful change for file {name} - skipping")
                                continue

                        logger.debug(f"Real change detected for {name}")
                        old_files[:] = [f for f in old_files if f['id'] != id_]
                        change_count += 1

                # Add/update
                if not parents:
                    continue
                res = find_game_and_path(drive_service, parents[0], game_id_to_path)
                if res:
                    path_parts, game_parent_id = res
                    new_raw_size = int(file.get('size', 0))
                    size_str = f"{new_raw_size / 1024 / 1024:.1f} MB" if new_raw_size > 1024 * 1024 else f"{new_raw_size / 1024:.1f} KB"
                    rel_folder_path = '/'.join(path_parts)
                    file_path = f"{rel_folder_path}/{name}" if path_parts else name

                    new_file = {
                        'name': name,
                        'id': id_,
                        'size': size_str,
                        'raw_size': new_raw_size,
                        'type': ext,
                        'path': file_path,
                        'modifiedTime': file.get('modifiedTime')
                    }
                    dev_name, game_name = game_id_to_path[game_parent_id]
                    files = folder_structure['developers'][dev_name]['games'][game_name]['files']
                    files.append(new_file)
                    logger.info(f"Added/updated file {name} in {game_name} / {dev_name}")

                    if ext in IMPORTANT_PATCH_EXTS:
                        new_changes.append(f"📦 UPDATED PATCH: {game_name}/{name}")
                        change_count += 1

                    dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)

        except Exception as e:
            logger.error(f"Incremental failed: {e}. Falling back to full scan.")
            use_changes = False
            folder_structure = last_folders.copy()

    if not use_changes:
        logger.info("Performing FULL scan...")
        new_change_token = drive_service.changes().getStartPageToken().execute().get('startPageToken')
        folder_structure['developers'] = {}
        dev_folders = list_files(drive_service, root_folder_id, folders_only=True)
        for i, dev_folder in enumerate(dev_folders, 1):
            dev_name = dev_folder['name']
            dev_id = dev_folder['id']
            logger.info(f"[{i}/{len(dev_folders)}] Processing developer: {dev_name}")
            folder_structure["developers"][dev_name] = {"id": dev_id, "games": {}}
            game_folders = list_files(drive_service, dev_id, folders_only=True)
            for j, game_folder in enumerate(game_folders, 1):
                game_name = game_folder['name']
                game_id = game_folder['id']
                game_files = recursive_list_files_with_path(drive_service, game_id)
                folder_structure["developers"][dev_name]["games"][game_name] = {"id": game_id, "files": game_files}
        change_count = len(folder_structure['developers'])
        new_changes.append("🔄 FULL DATABASE RESCAN PERFORMED.")

    metadata = folder_structure.setdefault('metadata', {})
    recent_changes = metadata.setdefault('recent_changes', [])
    for msg in reversed(new_changes):
        recent_changes.insert(0, msg)
    metadata['recent_changes'] = recent_changes[:10]
    if change_count > 0:
        metadata['version'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    game_count = sum(len(dev_data["games"]) for dev_data in folder_structure.get("developers", {}).values())
    logger.info(f"Processed {len(folder_structure.get('developers', {}))} developers, {game_count} games")
    return folder_structure, new_change_token, change_count

def main():
    logger.info("Starting sync...")
    drive_service = authenticate_drive()
    last_folders = load_last_folders()
    change_token = load_change_token()
    use_changes = bool(change_token and last_folders.get("developers"))
    logger.info(f"Incremental mode: {use_changes} (token: {bool(change_token)}, devs: {len(last_folders.get('developers', {}))})")

    folder_structure, new_change_token, change_count = index_game_folders(ROOT_FOLDER_ID, drive_service, last_folders, use_changes, change_token)

    save_database(folder_structure)
    if new_change_token:
        save_change_token(new_change_token)

    logger.info(f"Sync complete. Processed {change_count} changes.")

if __name__ == '__main__':
    main()