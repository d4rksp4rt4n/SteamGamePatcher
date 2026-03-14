"""
Patch folder indexing script to sync Google Drive folders with local JSON, including file lists in each game folder.
Version: 1.9.6
Changes in v1.9.6:
- Updated patch folder indexing script, enhancing the sync process for Google Drive folders with local JSON.
Changes in v1.9.5:
- When you move an old patch to the "Old" subfolder, the old entry is now REMOVED from the main files list
- This makes the frontend correctly detect the new version as an update
- All previous fixes kept (non-patch files indexed, recent_changes clean, no fake updates)
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

SERVICE_ACCOUNT_FILE = 'service-account.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CHANGE_TOKEN_FILE = 'database/data/change_token.txt'
ROOT_FOLDER_ID = '1a7jIAJoELzB3HpXNcuF0tGmDq1jqHs9-'
OUTPUT_JSON = 'database/data/patches_database.json'

MAX_RETRIES = 3
RETRY_DELAY_BASE = 1
TIME_TOLERANCE_MINUTES = 30
MIN_SIZE_DELTA_BYTES = 1024

IMPORTANT_PATCH_EXTS = {'.zip', '.7z', '.rar', '.exe'}
NON_PATCH_EXTS = {'.txt', '.pdf', '.docx', '.doc', '.rtf'}

debug_mode = '--debug' in sys.argv
logging.basicConfig(
    level=logging.DEBUG if debug_mode else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def authenticate_drive():
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds, cache_discovery=False)

def execute_with_retries(request, desc):
    for attempt in range(MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_BASE * (2 ** attempt))
                continue
            logger.error(f"Failed {desc} after {attempt+1} tries: {e}")
            raise
    raise RuntimeError(f"{desc} failed after {MAX_RETRIES} retries")

def list_files(service, folder_id, folders_only=False):
    results = []
    page_token = None
    q = f"'{folder_id}' in parents and trashed=false"
    if folders_only:
        q += " and mimeType='application/vnd.google-apps.folder'"
    while True:
        resp = execute_with_retries(
            service.files().list(
                q=q,
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,parents)",
                pageSize=100,
                pageToken=page_token
            ),
            f"list files in {folder_id}"
        )
        results.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return results

def get_file_tree(service, folder_id, current_path="", depth=0):
    indent = ' ' * depth
    logger.debug(f"{indent}Scanning folder: {current_path or '/'} (ID: {folder_id})")
    files = []
    items = list_files(service, folder_id)
    logger.debug(f"{indent}Found {len(items)} items")
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            subpath = f"{current_path}{item['name']}/" if current_path else f"{item['name']}/"
            logger.debug(f"{indent}- Subfolder: {item['name']} (ID: {item['id']})")
            files.extend(get_file_tree(service, item['id'], subpath, depth + 1))
        else:
            name = item['name']
            ext = Path(name).suffix.lower()
            if ext not in IMPORTANT_PATCH_EXTS | NON_PATCH_EXTS:
                logger.debug(f"{indent}- Skipping unsupported file: {name} ({ext})")
                continue
            raw_size = int(item.get('size', 0))
            size_display = f"{raw_size/1024/1024:.1f} MB" if raw_size > 1_048_576 else f"{raw_size/1024:.1f} KB"
            path = f"{current_path}{name}" if current_path else name
            modified = item.get('modifiedTime', 'N/A')
            logger.debug(f"{indent}- File: {name} ({ext}, {size_display}, modified {modified})")
            files.append({
                'id': item['id'],
                'name': name,
                'size': size_display,
                'raw_size': raw_size,
                'type': ext,
                'path': path,
                'modifiedTime': item.get('modifiedTime')
            })
    return files

def build_id_maps(db):
    dev_by_id = {}
    game_by_id = {}
    file_by_id = {}
    for dev_name, dev in db.get('developers', {}).items():
        dev_by_id[dev['id']] = dev_name
        for game_name, game in dev.get('games', {}).items():
            game_by_id[game['id']] = (dev_name, game_name)
            for f in game.get('files', []):
                file_by_id[f['id']] = (dev_name, game_name, f)
    return dev_by_id, game_by_id, file_by_id

def handle_deletion(file_id, db, dev_by_id, game_by_id, file_by_id, changes):
    if file_id in dev_by_id:
        name = dev_by_id.pop(file_id)
        del db['developers'][name]
        changes.append(f"🗑 DEVELOPER REMOVED: {name}")
        return True
    if file_id in game_by_id:
        dev, game = game_by_id.pop(file_id)
        del db['developers'][dev]['games'][game]
        changes.append(f"🗑 GAME REMOVED: {dev} / {game}")
        return True
    if file_id in file_by_id:
        dev, game, old_file = file_by_id.pop(file_id)
        files = db['developers'][dev]['games'][game]['files']
        files[:] = [f for f in files if f['id'] != file_id]
        changes.append(f"🗑 FILE REMOVED: {dev} / {game} / {old_file['name']}")
        return True
    return False

def index_incremental(service, db, change_token):
    logger.info("Running incremental update...")
    changes, new_token = [], None
    page_token = change_token
    while page_token:
        resp = execute_with_retries(
            service.changes().list(
                pageToken=page_token,
                spaces='drive',
                fields='nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,parents,trashed,size,modifiedTime))'
            ),
            "fetch changes"
        )
        changes.extend(resp.get('changes', []))
        page_token = resp.get('nextPageToken')
        new_token = resp.get('newStartPageToken') or new_token

    if not new_token:
        new_token = service.changes().getStartPageToken().execute()['startPageToken']

    dev_by_id, game_by_id, file_by_id = build_id_maps(db)
    change_log = []
    recent_changes = db.setdefault('metadata', {}).setdefault('recent_changes', [])

    for ch in changes:
        fid = ch['fileId']
        removed = ch.get('removed', False) or (ch.get('file') or {}).get('trashed', False)
        if removed:
            handle_deletion(fid, db, dev_by_id, game_by_id, file_by_id, change_log)
            dev_by_id, game_by_id, file_by_id = build_id_maps(db)
            continue

        file = ch.get('file')
        if not file:
            continue

        name = file['name']
        mime = file['mimeType']
        parents = file.get('parents', [])
        parent = parents[0] if parents else None
        mtime = file.get('modifiedTime')

        if mime == 'application/vnd.google-apps.folder':
            # Folder logic unchanged
            if fid in dev_by_id:
                old_name = dev_by_id[fid]
                if parent != ROOT_FOLDER_ID:
                    handle_deletion(fid, db, dev_by_id, game_by_id, file_by_id, change_log)
                elif old_name != name:
                    db['developers'][name] = db['developers'].pop(old_name)
                    db['developers'][name]['id'] = fid
                    change_log.append(f"✏ DEVELOPER RENAMED: {old_name} → {name}")
            elif fid in game_by_id:
                old_dev, old_game = game_by_id[fid]
                if not parent or parent not in dev_by_id:
                    handle_deletion(fid, db, dev_by_id, game_by_id, file_by_id, change_log)
                else:
                    new_dev = dev_by_id[parent]
                    if old_dev != new_dev or old_game != name:
                        gdata = db['developers'][old_dev]['games'].pop(old_game)
                        db['developers'][new_dev]['games'][name] = gdata
                        gdata['id'] = fid
                        change_log.append(f"✏ GAME MOVED/RENAMED: {old_dev}/{old_game} → {new_dev}/{name}")
            elif parent == ROOT_FOLDER_ID:
                db['developers'][name] = {'id': fid, 'games': {}}
                change_log.append(f"➕ NEW DEVELOPER: {name}")
            elif parent in dev_by_id:
                devn = dev_by_id[parent]
                logger.debug(f"Indexing new game: {devn} / {name} (ID: {fid})")
                
                game_files = get_file_tree(service, fid, depth=1)
                db['developers'][devn]['games'][name] = {'id': fid, 'files': game_files}
                
                # === CRITICAL FIX: Mark patches in brand new games as NEW PATCH ===
                for f in game_files:
                    if f.get('type') in IMPORTANT_PATCH_EXTS:
                        msg = f"➕ NEW PATCH: {devn}/{name}/{f['name']}"
                        change_log.append(msg)
                        recent_changes.append((datetime.datetime.now().isoformat(), name, msg))
                        logger.info(msg)
                    else:
                        logger.debug(f"Added install note for new game: {devn}/{name}/{f['name']}")
                
                msg_game = f"➕ NEW GAME: {devn} / {name}"
                change_log.append(msg_game)
                recent_changes.append((datetime.datetime.now().isoformat(), name, msg_game))
                logger.info(msg_game)
                
                dev_by_id, game_by_id, file_by_id = build_id_maps(db)
                continue

        # === FILE ===
        ext = Path(name).suffix.lower()
        if ext not in IMPORTANT_PATCH_EXTS | NON_PATCH_EXTS:
            continue

        is_real_patch = ext in IMPORTANT_PATCH_EXTS

        if fid in file_by_id:
            dev, game, existing = file_by_id[fid]
            game_files = db['developers'][dev]['games'][game]['files']

            name_changed = name != existing['name']
            parent_changed = parent != db['developers'][dev]['games'][game]['id']

            # === SPECIAL "OLD" FOLDER HANDLING ===
            moved_to_old = False
            if parent_changed and parent:
                try:
                    parent_info = execute_with_retries(
                        service.files().get(fileId=parent, fields='name'), 
                        "get parent name"
                    )
                    if "Old" in parent_info.get('name', ''):
                        moved_to_old = True
                except:
                    pass

            if moved_to_old:
                # Remove old file entry (this is what makes the frontend detect an update)
                files = db['developers'][dev]['games'][game]['files']
                files[:] = [f for f in files if f['id'] != fid]
                change_log.append(f"🗑 OLD PATCH ARCHIVED: {dev}/{game}/{name}")
                logger.debug(f"→ Removed old patch moved to Old folder: {name}")
                dev_by_id, game_by_id, file_by_id = build_id_maps(db)
                continue

            # === STRICT CHANGE DETECTION (normal cases) ===
            time_newer = False
            time_delta_min = 0
            if mtime and existing.get('modifiedTime'):
                try:
                    old_dt = datetime.datetime.fromisoformat(existing['modifiedTime'].replace('Z', '+00:00'))
                    new_dt = datetime.datetime.fromisoformat(mtime.replace('Z', '+00:00'))
                    delta = new_dt - old_dt
                    time_delta_min = delta.total_seconds() / 60
                    time_newer = time_delta_min > TIME_TOLERANCE_MINUTES
                except:
                    time_newer = mtime > existing['modifiedTime']

            size_changed = False
            size_delta = 0
            new_size = int(file.get('size', 0))
            old_size = existing.get('raw_size', 0)
            size_delta = new_size - old_size
            size_changed = abs(size_delta) >= MIN_SIZE_DELTA_BYTES

            logger.debug(f"[DETECT] {name} - name_changed={name_changed}, parent_changed={parent_changed}, "
                         f"time_delta={time_delta_min:.1f}min (> {TIME_TOLERANCE_MINUTES}? {time_newer}), "
                         f"size_delta={size_delta} bytes (>= {MIN_SIZE_DELTA_BYTES}? {size_changed})")

            if not (name_changed or (time_newer and size_changed)):
                logger.debug(f"→ Skipped {name} (no real change)")
                continue

            # Real change
            idx = next(i for i, f in enumerate(game_files) if f['id'] == fid)
            game_files[idx].update({
                'name': name,
                'path': f"{ '/'.join(existing['path'].split('/')[:-1]) }/{name}" if '/' in existing['path'] else name,
                'modifiedTime': mtime,
                'size': f"{new_size/1024/1024:.1f} MB" if new_size > 1_048_576 else f"{new_size/1024:.1f} KB",
                'raw_size': new_size
            })

            if is_real_patch:
                msg = f"📦 UPDATED PATCH: {dev}/{game}/{name}"
                change_log.append(msg)
                recent_changes.append((datetime.datetime.now().isoformat(), game, msg))
                logger.info(msg)
            else:
                logger.debug(f"Updated non-patch file (Install Note): {dev}/{game}/{name}")

        else:
            # New file (unchanged)
            if not parents:
                continue
            cur = parents[0]
            steps = 0
            while cur and cur not in game_by_id and steps < 20:
                steps += 1
                try:
                    info = execute_with_retries(service.files().get(fileId=cur, fields='name,parents'), "get parent")
                    if info['name'] == 'Old':
                        break
                    parents_list = info.get('parents', [])
                    if not parents_list:
                        break
                    cur = parents_list[0]
                except:
                    break
            if cur in game_by_id:
                devn, gamen = game_by_id[cur]
                raw = int(file.get('size', 0))
                sz = f"{raw/1024/1024:.1f} MB" if raw > 1_048_576 else f"{raw/1024:.1f} KB"
                path = f"{gamen}/{name}"
                newf = {
                    'id': fid,
                    'name': name,
                    'size': sz,
                    'raw_size': raw,
                    'type': ext,
                    'path': path,
                    'modifiedTime': mtime
                }
                db['developers'][devn]['games'][gamen]['files'].append(newf)

                if is_real_patch:
                    msg = f"➕ NEW PATCH: {devn}/{gamen}/{name}"
                    change_log.append(msg)
                    recent_changes.append((datetime.datetime.now().isoformat(), gamen, msg))
                    logger.info(msg)
                else:
                    logger.debug(f"Added new non-patch file (Install Note): {devn}/{gamen}/{name}")

    return db, new_token, change_log

def index_full(service, db):
    logger.info("Full database rebuild...")
    db['developers'] = {}
    devs = list_files(service, ROOT_FOLDER_ID, folders_only=True)
    for i, devf in enumerate(devs, 1):
        dname = devf['name']
        logger.info(f"[{i}/{len(devs)}] {dname}")
        db['developers'][dname] = {'id': devf['id'], 'games': {}}
        games = list_files(service, devf['id'], folders_only=True)
        for g in games:
            gname = g['name']
            logger.debug(f" Indexing game: {dname}/{gname} (ID: {g['id']})")
            db['developers'][dname]['games'][gname] = {
                'id': g['id'],
                'files': get_file_tree(service, g['id'], depth=2)
            }
    return db

def main():
    logger.info("Starting sync...")
    service = authenticate_drive()

    db = {}
    if os.path.exists(OUTPUT_JSON):
        try:
            with open(OUTPUT_JSON, encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    db = json.loads(content)
                    logger.info(f"Loaded {len(db.get('developers',{}))} developers")
                else:
                    logger.warning(f"{OUTPUT_JSON} is empty. Starting with empty db.")
        except Exception as e:
            logger.error(f"Failed to load {OUTPUT_JSON}: {e}. Starting with empty db.")

    token = None
    if os.path.exists(CHANGE_TOKEN_FILE):
        with open(CHANGE_TOKEN_FILE) as f:
            token = f.read().strip()

    use_incremental = bool(token and db.get('developers'))
    change_log = []

    if use_incremental:
        db, new_token, change_log = index_incremental(service, db, token)
    else:
        db = index_full(service, db)
        new_token = execute_with_retries(service.changes().getStartPageToken(), "get new start token")['startPageToken']
        change_log = ["🔄 FULL DATABASE RESCAN PERFORMED"]

    # Clean recent_changes
    metadata = db.setdefault('metadata', {})
    all_recent = []
    seen = set()
    for item in metadata.get('recent_changes', []):
        if isinstance(item, (list, tuple)) and len(item) == 3:
            ts, game, msg = item
            if game not in seen:
                all_recent.append((ts, game, msg))
                seen.add(game)
    for item in change_log:
        if isinstance(item, tuple) and len(item) == 3:
            ts, game, msg = item
            if game not in seen:
                all_recent.append((ts, game, msg))
                seen.add(game)
    all_recent.sort(reverse=True)
    metadata['recent_changes'] = all_recent[:10]
    metadata['last_sync'] = datetime.datetime.now().isoformat()

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    if new_token:
        with open(CHANGE_TOKEN_FILE, 'w') as f:
            f.write(new_token)

    logger.info(f"Sync done. {len(change_log)} changes.")
    if metadata.get('recent_changes'):
        logger.info("Recent changes (for frontend - last 10 unique games):")
        for _, game, msg in metadata['recent_changes']:
            logger.info(f"  {msg}")

if __name__ == '__main__':
    main()
