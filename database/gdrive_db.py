"""
Patch folder indexing script to sync Google Drive folders with local JSON, including file lists in each game folder.
Version: 1.1.9
Changes:
- Implemented proper incremental updates using changes API.
- Only process changed items in incremental mode.
- Fallback to full scan if changes API fails or no token.
- Handle additions, deletions, renames, and moves.
- Ignore irrelevant changes outside the root folder structure.
- Added recursive scanning of subfolders within game folders, excluding folders named "Old".
- Enhanced incremental updates to handle files in subfolders by traversing the parent chain and excluding paths containing "Old" folders.
- Added 'path' field to each file entry to preserve relative folder structure information within the game, avoiding flat list confusion.
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
BATCH_SIZE = 100
RATE_LIMIT_DELAY = 1
# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
def is_file_locked(file_path):
    if not win32file:
        logger.debug("pywin32 not installed, falling back to basic file handling")
        return False
    try:
        handle = win32file.CreateFile(
            file_path, win32file.GENERIC_READ, 0, None,
            win32file.OPEN_EXISTING, 0, None)
        win32file.CloseHandle(handle)
        return False
    except pywintypes.error:
        return True
def is_valid_json(file_path):
    if not os.path.exists(file_path):
        logger.debug(f"{file_path} does not exist")
        return False
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        logger.debug(f"{file_path} is valid JSON")
        return True
    except json.JSONDecodeError as e:
        logger.debug(f"{file_path} is invalid JSON: {e}")
        return False
    except Exception as e:
        logger.debug(f"Error reading {file_path}: {e}")
        return False
def authenticate_drive():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logger.error(f"Missing {SERVICE_ACCOUNT_FILE}. Create it from Google Cloud Console.")
        sys.exit(1)
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        logger.info("Drive service authenticated successfully")
        return service
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)
def execute_with_retries(request, operation_name):
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Starting {operation_name} attempt {attempt + 1}/{MAX_RETRIES}")
            response = request.execute()
            logger.debug(f"{operation_name} succeeded")
            return response
        except HttpError as e:
            logger.warning(f"{operation_name} attempt {attempt + 1} failed: {e.resp.status} - {e}")
            if e.resp.status in [429, 500] and attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.info(f"Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"{operation_name} failed after {MAX_RETRIES} attempts")
                raise
    raise Exception(f"Failed {operation_name} after {MAX_RETRIES} retries")
def list_files(drive_service, folder_id, folders_only=False):
    try:
        logger.debug(f"Listing {'folders' if folders_only else 'files'} in folder {folder_id}...")
        results = []
        page_token = None
        page_num = 0
        query = f"'{folder_id}' in parents and trashed=false"
        if folders_only:
            query += " and mimeType='application/vnd.google-apps.folder'"
        while True:
            page_num += 1
            logger.debug(f"Fetching page {page_num} for {folder_id}")
            response = execute_with_retries(
                drive_service.files().list(
                    q=query, fields="nextPageToken, files(id, name, mimeType, size)",
                    pageSize=100, pageToken=page_token
                ),
                f"list_files page {page_num} for folder {folder_id}"
            )
            page_results = response.get('files', [])
            results.extend(page_results)
            logger.debug(f"Page {page_num} returned {len(page_results)} items")
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        logger.info(f"Found {len(results)} {'folders' if folders_only else 'files'} in folder {folder_id}")
        return results
    except HttpError as e:
        logger.error(f"Listing files failed: {e}")
        raise
def recursive_list_files_with_path(drive_service, folder_id, current_path='', ignore_folder_names=['Old']):
    results = []
    items = list_files(drive_service, folder_id, folders_only=False)
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            if item['name'] in ignore_folder_names:
                continue
            sub_path = f"{current_path}{item['name']}/" if current_path else f"{item['name']}/"
            sub_files = recursive_list_files_with_path(drive_service, item['id'], sub_path, ignore_folder_names)
            results.extend(sub_files)
        else:
            ext = Path(item['name']).suffix.lower()
            if ext in ['.zip', '.7z', '.rar', '.exe']:
                size_str = item.get('size', 'Unknown')
                if isinstance(size_str, str) and size_str.isdigit():
                    size = int(size_str)
                    size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.1f} KB"
                file_path = f"{current_path}{item['name']}" if current_path else item['name']
                results.append({
                    'name': item['name'],
                    'id': item['id'],
                    'size': size_str,
                    'type': ext,
                    'path': file_path
                })
    return results
def find_game_and_path(drive_service, start_parent_id, game_id_to_path):
    path_parts = []
    current_id = start_parent_id
    while current_id and current_id not in game_id_to_path:
        try:
            folder_info = execute_with_retries(
                drive_service.files().get(fileId=current_id, fields='name,parents'),
                f"get folder info for {current_id}"
            )
            name = folder_info['name']
            if name == 'Old':
                return None
            parents = folder_info.get('parents', [])
            if not parents:
                return None
            path_parts.append(name)
            current_id = parents[0]
        except HttpError as e:
            logger.warning(f"Error getting folder {current_id}: {e}")
            return None
    if current_id not in game_id_to_path:
        return None
    return path_parts[::-1], current_id
def get_changes(drive_service, change_token):
    try:
        logger.debug(f"Fetching changes since token {change_token}")
        changes = []
        while True:
            response = execute_with_retries(
                drive_service.changes().list(
                    pageToken=change_token,
                    spaces='drive',
                    fields='nextPageToken,newStartPageToken,changes(file(id,name,mimeType,parents,trashed,size))'
                ),
                "get_changes"
            )
            page_changes = response.get('changes', [])
            changes.extend(page_changes)
            logger.debug(f"Got {len(page_changes)} changes")
            change_token = response.get('nextPageToken') or response.get('newStartPageToken')
            if not response.get('nextPageToken'):
                break
        logger.info(f"Found {len(changes)} changes since token {change_token}")
        return changes, change_token
    except HttpError as e:
        logger.error(f"Fetching changes failed: {e}")
        raise
def load_change_token():
    try:
        if os.path.exists(CHANGE_TOKEN_FILE):
            with open(CHANGE_TOKEN_FILE, 'r') as f:
                token = f.read().strip()
            if token:
                logger.info(f"Loaded change token: {token[:10]}...")
                return token
            else:
                logger.warning(f"Empty {CHANGE_TOKEN_FILE}")
        logger.debug(f"No {CHANGE_TOKEN_FILE}, will fetch initial token")
        return None
    except Exception as e:
        logger.error(f"Loading {CHANGE_TOKEN_FILE} failed: {e}")
        return None
        
def save_change_token(token):
    try:
        os.makedirs(os.path.dirname(CHANGE_TOKEN_FILE), exist_ok=True)
        with open(CHANGE_TOKEN_FILE, 'w') as f:
            f.write(token)
        logger.debug(f"Saved change token: {token}")
    except Exception as e:
        logger.error(f"Saving {CHANGE_TOKEN_FILE} failed: {e}")
        raise
def load_last_folders():
    if not is_valid_json(OUTPUT_JSON):
        logger.debug(f"No valid {OUTPUT_JSON}, assuming first run")
        return {"developers": {}, "metadata": {}}
    try:
        with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        devs = data.get('developers', {})
        logger.info(f"Loaded {len(devs)} developers from {OUTPUT_JSON}")
        return data
    except Exception as e:
        logger.error(f"Loading {OUTPUT_JSON} failed: {e}")
        return {"developers": {}, "metadata": {}}
        
def save_database(folder_structure):
    try:
        os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
        temp_file = OUTPUT_JSON + '.tmp'
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(folder_structure, f, indent=4, ensure_ascii=False)
        if os.path.exists(OUTPUT_JSON):
            os.remove(OUTPUT_JSON)
        os.rename(temp_file, OUTPUT_JSON)
        logger.debug(f"Saved database to {OUTPUT_JSON}")
    except Exception as e:
        logger.error(f"Saving {OUTPUT_JSON} failed: {e}")
        raise
def build_id_maps(folder_structure):
    dev_id_to_name = {}
    game_id_to_path = {} # id: (dev_name, game_name)
    file_id_to_game = {} # id: (dev_name, game_name)
    for dev_name, dev in folder_structure.get('developers', {}).items():
        dev_id = dev['id']
        dev_id_to_name[dev_id] = dev_name
        for game_name, game in dev['games'].items():
            game_id = game['id']
            game_id_to_path[game_id] = (dev_name, game_name)
            for f in game['files']:
                file_id = f['id']
                file_id_to_game[file_id] = (dev_name, game_name)
    return dev_id_to_name, game_id_to_path, file_id_to_game
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
                if 'file' not in change:
                    continue
                file = change['file']
                id_ = file['id']
                name = file.get('name', '')
                mime = file['mimeType']
                trashed = file.get('trashed', False)
                parents = file.get('parents', [])
                parent = parents[0] if parents else None
                if trashed:
                    if id_ in dev_id_to_name:
                        dev_name = dev_id_to_name[id_]
                        logger.info(f"Deleted developer: {dev_name}")
                        new_changes.append(f"{dev_name} - Deleted developer")
                        del folder_structure['developers'][dev_name]
                    elif id_ in game_id_to_path:
                        dev_name, game_name = game_id_to_path[id_]
                        logger.info(f"Deleted game: {game_name} in {dev_name}")
                        new_changes.append(f"{game_name} - Deleted game from {dev_name}")
                        del folder_structure['developers'][dev_name]['games'][game_name]
                    elif id_ in file_id_to_game:
                        dev_name, game_name = file_id_to_game[id_]
                        logger.info(f"Deleted file {name} in {game_name} / {dev_name}")
                        new_changes.append(f"{game_name} - Deleted file {name}")
                        files = folder_structure['developers'][dev_name]['games'][game_name]['files']
                        folder_structure['developers'][dev_name]['games'][game_name]['files'] = [f for f in files if f['id'] != id_]
                    else:
                        logger.debug(f"Ignoring trashed irrelevant item: {id_}")
                    # Rebuild maps after change
                    dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                    change_count += 1
                    continue
                # Not trashed
                if mime == 'application/vnd.google-apps.folder':
                    if id_ in dev_id_to_name:
                        if parent != root_folder_id:
                            dev_name = dev_id_to_name[id_]
                            logger.info(f"Removed developer {dev_name} (moved out)")
                            new_changes.append(f"{dev_name} - Removed developer (moved out)")
                            del folder_structure['developers'][dev_name]
                            dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                            change_count += 1
                            continue
                        else:
                            old_name = dev_id_to_name[id_]
                            if old_name != name:
                                logger.info(f"Renamed developer {old_name} to {name}")
                                new_changes.append(f"{old_name} - Renamed developer to {name}")
                                folder_structure['developers'][name] = folder_structure['developers'].pop(old_name)
                    elif id_ in game_id_to_path:
                        if parent is None or parent not in dev_id_to_name:
                            old_dev, old_name = game_id_to_path[id_]
                            logger.info(f"Removed game {old_name} from {old_dev} (moved out)")
                            new_changes.append(f"{old_name} - Removed game from {old_dev} (moved out)")
                            del folder_structure['developers'][old_dev]['games'][old_name]
                            dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                            change_count += 1
                            continue
                        else:
                            old_dev, old_name = game_id_to_path[id_]
                            new_dev = dev_id_to_name[parent]
                            if old_dev != new_dev or old_name != name:
                                logger.info(f"Moved/renamed game {old_name} from {old_dev} to {name} in {new_dev}")
                                new_changes.append(f"{old_name} - Moved/renamed game to {name} in {new_dev}")
                                game_data = folder_structure['developers'][old_dev]['games'].pop(old_name)
                                folder_structure['developers'][new_dev]['games'][name] = game_data
                                game_data['id'] = id_
                    else:
                        # New folder
                        if parent == root_folder_id:
                            logger.info(f"Added developer: {name}")
                            new_changes.append(f"{name} - Added developer")
                            folder_structure['developers'][name] = {'id': id_, 'games': {}}
                        elif parent in dev_id_to_name:
                            dev_name = dev_id_to_name[parent]
                            logger.info(f"Added game: {name} to {dev_name}")
                            new_changes.append(f"{name} - Added game to {dev_name}")
                            folder_structure['developers'][dev_name]['games'][name] = {'id': id_, 'files': []}
                        else:
                            logger.debug(f"Ignoring new folder {name} with parent {parent}")
                            continue
                    # Rebuild maps after change
                    dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                    change_count += 1
                else:
                    # File
                    removed = False
                    if id_ in file_id_to_game:
                        old_dev, old_game = file_id_to_game[id_]
                        old_files = folder_structure['developers'][old_dev]['games'][old_game]['files']
                        folder_structure['developers'][old_dev]['games'][old_game]['files'] = [f for f in old_files if f['id'] != id_]
                        removed = True
                        logger.info(f"Removed file {name} from old location {old_game} / {old_dev}")
                        new_changes.append(f"{old_game} - Removed file {name} from {old_dev}")
                    if not parents:
                        if removed:
                            change_count += 1
                        continue
                    parent = parents[0]
                    res = find_game_and_path(drive_service, parent, game_id_to_path)
                    if res is None:
                        if removed:
                            change_count += 1
                        continue
                    path_parts, game_parent_id = res
                    ext = Path(name).suffix.lower()
                    if ext not in ['.zip', '.7z', '.rar', '.exe']:
                        if removed:
                            change_count += 1
                        continue
                    size_str = file.get('size', 'Unknown')
                    if isinstance(size_str, str) and size_str.isdigit():
                        size = int(size_str)
                        size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.1f} KB"
                    rel_folder_path = '/'.join(path_parts)
                    file_path = f"{rel_folder_path}/{name}" if path_parts else name
                    new_file = {
                        'name': name,
                        'id': id_,
                        'size': size_str,
                        'type': ext,
                        'path': file_path
                    }
                    dev_name, game_name = game_id_to_path[game_parent_id]
                    files = folder_structure['developers'][dev_name]['games'][game_name]['files']
                    files.append(new_file)
                    logger.info(f"Added/updated file {name} in {game_name} / {dev_name}")
                    new_changes.append(f"{game_name} - Update to {name}")
                    # Rebuild maps after change
                    dev_id_to_name, game_id_to_path, file_id_to_game = build_id_maps(folder_structure)
                    change_count += 1
        except Exception as e:
            logger.error(f"Incremental mode failed: {e}. Falling back to full scan.")
            use_changes = False
            folder_structure = last_folders.copy()  # Preserve base with extras
    if not use_changes:
        logger.info("Full scan")
        try:
            new_change_token = drive_service.changes().getStartPageToken().execute().get('startPageToken')
        except Exception as e:
            logger.error(f"Failed to get start page token: {e}")
            raise
        folder_structure['developers'] = {}
        # Fetch developer folders
        logger.info("Fetching developer folders...")
        dev_folders = list_files(drive_service, root_folder_id, folders_only=True)
        logger.info(f"Got {len(dev_folders)} developer folders")
        for i, dev_folder in enumerate(dev_folders, 1):
            dev_name = dev_folder['name']
            dev_id = dev_folder['id']
            logger.info(f"[{i}/{len(dev_folders)}] Processing developer: {dev_name} (ID: {dev_id})")
            folder_structure["developers"][dev_name] = {"id": dev_id, "games": {}}
            game_folders = list_files(drive_service, dev_id, folders_only=True)
            logger.info(f"Found {len(game_folders)} game folders in {dev_name}")
            for j, game_folder in enumerate(game_folders, 1):
                game_name = game_folder['name']
                game_id = game_folder['id']
                logger.info(f" [{j}/{len(game_folders)}] Processing game: {game_name} (ID: {game_id})")
                game_files = recursive_list_files_with_path(drive_service, game_id, '', ['Old'])
                logger.info(f" Found {len(game_files)} files in {game_name}")
                files_list = game_files
                folder_structure["developers"][dev_name]["games"][game_name] = {
                    "id": game_id,
                    "files": files_list
                }
        change_count = len(folder_structure['developers'])
        new_changes.append("Full database scan performed")
        # Merge extras from previous (last_folders)
        game_id_to_extra = {}
        for dev in last_folders.get('developers', {}).values():
            for game in dev.get('games', {}).values():
                gid = game.get('id')
                if gid:
                    extra = {k: v for k, v in game.items() if k not in ['id', 'files']}  # Preserve non-structural keys
                    game_id_to_extra[gid] = extra
        # Apply to new structure
        merged_count = 0
        for dev in folder_structure['developers'].values():
            for game in dev['games'].values():
                gid = game.get('id')
                if gid in game_id_to_extra:
                    game.update(game_id_to_extra[gid])
                    merged_count += 1
        logger.info(f"Merged extras for {merged_count} games during full scan")
    # Update metadata
    metadata = folder_structure.setdefault('metadata', {})
    recent_changes = metadata.setdefault('recent_changes', [])
    recent_changes.extend(new_changes)
    if len(recent_changes) > 10:
        recent_changes = recent_changes[-10:]
    metadata['version'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Summary
    game_count = sum(len(dev_data["games"]) for dev_data in folder_structure.get("developers", {}).values())
    logger.info(f"Processed {len(folder_structure.get('developers', {}))} developers, {game_count} games")
    return folder_structure, new_change_token, change_count
def main():
    logger.info("Starting patch database builder...")
    drive_service = authenticate_drive()
    last_folders = load_last_folders()
    change_token = load_change_token()
    use_changes = bool(change_token and last_folders.get("developers"))
    logger.info(f"Incremental mode: {use_changes} (token: {bool(change_token)}, devs: {len(last_folders.get('developers', {}))} )")
    folder_structure, new_change_token, change_count = index_game_folders(
        ROOT_FOLDER_ID, drive_service, last_folders, use_changes, change_token)
    save_database(folder_structure)
    save_change_token(new_change_token)
    logger.info(f"Database updated in {OUTPUT_JSON} with {change_count} changes")
if __name__ == '__main__':
    main()