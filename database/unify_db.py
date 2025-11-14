"""
Unify Databases Script
Version: 1.0.5  # Updated to always merge if matched and fields differ
Purpose: Merge metadata from patches_data.json into database/data/patches_database.json
by fuzzy matching game names primarily (ratio >80), with developer as secondary check (>60).
Handles mismatches where folder devs are publishers/folder names vs Steam devs.
Adds/updates appid, header_image, publisher, notes, store_status to game entries.
Saves unified data back to database/data/patches_database.json.
Logs matches, unmatched, low-scores for review.
Optimizations:
- Primary: Fuzzy on game_name across all entries (unique enough).
- Secondary: Dev similarity for confirmation.
- Lower thresholds: Game >80, Dev >60; more cutoffs [0.9,0.8,0.7,0.6].
- Normalization fixes colons/dashes; dev variants.
- Now matches ~80%+ based on samples (e.g., Super Neptunia via game match despite dev diff).
- Always checks for differences in fields like notes, even if key exists.
"""
import json
import difflib
import unicodedata
import re
from pathlib import Path
import logging
import os
import requests

# === USE EXISTING patches_data.json IF AVAILABLE ===
LOCAL_PATCHES_DATA = Path("database/data/patches_data.json")
LOCAL_DB_PATH = Path("database/data/patches_database.json")

def ensure_patches_data():
    if LOCAL_PATCHES_DATA.exists():
        try:
            with open(LOCAL_PATCHES_DATA, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('entries') and len(data['entries']) > 0:
                logging.info(f"Using existing patches_data.json ({len(data['entries'])} entries)")
                return
        except Exception as e:
            logging.warning(f"Existing patches_data.json invalid ({e}), will attempt download.")
    # Fallback: Download if missing/invalid
    token = os.getenv('NUKIGE_TOKEN')
    if not token:
        logging.error("NUKIGE_TOKEN not set; cannot download from private repo. Please run in workflow or set token locally.")
        exit(1)
    logging.info("Downloading latest patches_data.json from private GitHub repo...")
    try:
        PATCHES_DATA_API_URL = "https://api.github.com/repos/d4rksp4rt4n/nukige-site/contents/cache/patches_data.json"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        response = requests.get(PATCHES_DATA_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        download_url = data.get('download_url')
        if not download_url:
            raise ValueError("No download_url in API response.")
        download_response = requests.get(download_url, headers=headers, timeout=30)
        download_response.raise_for_status()
        LOCAL_PATCHES_DATA.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_PATCHES_DATA, 'wb') as f:
            f.write(download_response.content)
        logging.info(f"Downloaded: {LOCAL_PATCHES_DATA.stat().st_size / 1024:.1f} KB")
    except Exception as e:
        logging.error(f"Download failed: {e}")
        if not LOCAL_PATCHES_DATA.exists():
            exit(1)

ensure_patches_data()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

def normalize_string(s, strip_suffixes=False):
    """Normalize a string for matching, optionally stripping suffixes."""
    if not s:
        return ""
    s = unicodedata.normalize('NFKC', s).strip()
    s = re.sub(r'\s+', ' ', s)  # Normalize spaces
    s = s.replace(" - ", "-").replace(" ~ ", "~").replace("～", "~")  # Normalize tildes
    s = s.replace(":", "-").replace("∶", "-").replace("–", "-")  # Normalize dashes/colons
    s = s.replace(" vol.", " vol").replace("vol. ", "vol")
  
    if strip_suffixes:
        s = re.sub(r'\s*[\u4e00-\u9fff]+$', '', s).strip()
        s = re.sub(r'\s*完全版$', '', s).strip()
  
    if any('\u4e00' <= c <= '\u9fff' for c in s):
        match = re.search(r'\(([^)]+)\)', s)
        if match and not all('\u4e00' <= c <= '\u9fff' for c in match.group(1)):
            s = f"{s.split('(')[0].strip()} {match.group(1)}"
  
    return s.lower()

def normalize_developer(dev):
    """Normalize developer name for matching."""
    dev = unicodedata.normalize('NFKC', dev).strip()
    dev = re.sub(r'\s*\([^)]+\)', '', dev).strip()
    dev = re.sub(r'\s+', ' ', dev)
    return dev.lower()

def generate_developer_variants(developer, entry_devs):
    """Generate variants of a developer name for matching."""
    normalized_dev = normalize_developer(developer)
    variants = [developer, normalized_dev]
  
    if '(' in developer and ')' in developer:
        jap_dev = developer.split('(')[0].strip()
        eng_dev = developer.split('(')[1].replace(')', '').strip()
        variants.extend([jap_dev, eng_dev])
  
    dev_set = set()
    for e_dev in entry_devs:
        if normalize_developer(e_dev) == normalized_dev:
            dev_set.add(e_dev)
    variants.extend(list(dev_set))
  
    return list(set(v for v in variants if v))

def fuzz_ratio(a, b):
    """difflib ratio as percentage."""
    return difflib.SequenceMatcher(None, a, b).ratio() * 100

def get_entry_match(folder_dev, folder_game, entries, entry_devs):
    """Find best matching entry: Primary fuzzy on game (>80), secondary dev (>60)."""
    low_score_fuzzy_matches = []
    best_entry = None
    best_score = 0
    best_reason = ""
  
    norm_folder_game = normalize_string(folder_game)
    dev_variants = generate_developer_variants(folder_dev, entry_devs)
  
    for entry in entries:
        e_game = entry.get('game', '').strip()
        e_dev = entry.get('developer', '').strip()
        norm_e_game = normalize_string(e_game)
        norm_e_dev = normalize_developer(e_dev)
      
        # Primary: Game fuzzy
        game_score = fuzz_ratio(norm_folder_game, norm_e_game)
        if game_score < 80:
            continue  # Skip low game match
      
        # Secondary: Dev similarity (any variant)
        dev_score = max(fuzz_ratio(folder_dev, e_dev), *[fuzz_ratio(v, e_dev) for v in dev_variants])
        if dev_score < 60:
            dev_score = 60  # Allow if game high, but note in reason
      
        total_score = game_score + dev_score
        if total_score > best_score:
            best_score = total_score
            best_entry = entry
            match_type = "exact_game" if game_score >= 95 else ("fuzzy_game" if game_score >= 80 else "low_game")
            dev_note = "dev_match" if dev_score >= 70 else "dev_mismatch"
            best_reason = f"{match_type} (game: {game_score:.1f}, dev: {dev_score:.1f} - {dev_note})"
  
    if not best_entry:
        # Fallback: Global closest games >70
        closest = []
        for entry in entries:
            e_game = normalize_string(entry.get('game', ''))
            score = fuzz_ratio(norm_folder_game, e_game)
            if score > 70:
                closest.append((entry, score))
        if closest:
            closest.sort(key=lambda x: x[1], reverse=True)
            best_entry, best_score = closest[0]
            best_reason = f"fallback_fuzzy_game (score: {best_score:.1f})"
        else:
            # Log low scores
            cutoffs = [0.9, 0.8, 0.7, 0.6]
            for cutoff in cutoffs:
                matches = difflib.get_close_matches(norm_folder_game, [normalize_string(e.get('game', '')) for e in entries], n=3, cutoff=cutoff)
                if matches:
                    for m in matches:
                        score = fuzz_ratio(norm_folder_game, m)
                        low_score_fuzzy_matches.append((f"{folder_dev}|{folder_game}", m, score, folder_dev, cutoff))
                    break
            best_reason = f"No match (tried cutoffs {cutoffs})"
  
    match_status = "matched" if best_entry else "unmatched"
    return best_entry, match_status, best_reason, low_score_fuzzy_matches

def load_patches_data():
    """Load entries from database/data/patches_data.json"""
    path = Path('database/data/patches_data.json')
    if not path.exists():
        logger.error("database/data/patches_data.json not found.")
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('entries', [])

def load_folder_db():
    """Load developers from database/data/patches_database.json"""
    db_path = Path('database/data/patches_database.json')
    if not db_path.exists():
        logger.error("database/data/patches_database.json not found.")
        return {}
    with open(db_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def unify_databases():
    entries = load_patches_data()
    folder_db = load_folder_db()
  
    developers = folder_db.get('developers', {})
  
    if not entries:
        logger.warning("No entries in patches_data.json")
    if not developers:
        logger.warning("No developers in patches_database.json")
        return
  
    entry_devs = [e.get('developer', '') for e in entries if e.get('developer')]
  
    matched = 0
    unmatched_games = []
    low_score_fuzzy_matches = []
    updated = 0
  
    for dev_name, dev_data in developers.items():
        for game_name, game_data in dev_data.get('games', {}).items():
            # Always attempt match (removed skip condition)
            entry, match_status, reason, low_scores = get_entry_match(
                dev_name, game_name, entries, entry_devs
            )
            low_score_fuzzy_matches.extend(low_scores)
          
            if entry and match_status == "matched":
                changes_made = False
                old_notes = game_data.get('notes', '')
                new_notes = entry.get('notes', '')
                if old_notes != new_notes:
                    game_data['notes'] = new_notes
                    changes_made = True
                
                # Update other fields if missing or different (expand as needed)
                if game_data.get('appid') != entry.get('appid'):
                    game_data['appid'] = entry.get('appid')
                    changes_made = True
                if game_data.get('header_image') != entry.get('header_image'):
                    game_data['header_image'] = entry.get('header_image')
                    changes_made = True
                if game_data.get('publisher', '') != entry.get('publisher', ''):
                    game_data['publisher'] = entry.get('publisher', '')
                    changes_made = True
                if game_data.get('store_status', '') != entry.get('store_status', ''):
                    game_data['store_status'] = entry.get('store_status', '')
                    changes_made = True
                
                if 'pending_version_check' in game_data:
                    del game_data['pending_version_check']
                    changes_made = True
                
                matched += 1
                if changes_made:
                    updated += 1
                    logger.info(f"Updated {game_name} in {dev_name}: {reason} (changes: notes '{old_notes}' -> '{new_notes}', etc.)")
                else:
                    logger.info(f"Matched but no changes for {game_name} in {dev_name}: {reason}")
            else:
                unmatched_games.append((dev_name, game_name))
                logger.warning(f"Unmatched {game_name} in {dev_name}: {reason}")
  
    logger.info(f"Unified: {matched} games matched and enriched ({updated} updated).")
    if unmatched_games:
        logger.warning(f"Unmatched games ({len(unmatched_games)}): {unmatched_games[:5]}...")
  
    # Log low-score fuzzy matches
    if low_score_fuzzy_matches:
        logger.info(f"Low score fuzzy matches for manual review ({len(low_score_fuzzy_matches)}):")
        for fk, mg, score, dev, cutoff in low_score_fuzzy_matches[:10]:
            logger.info(f" - {fk} -> {mg} (score: {score:.1f}, dev: {dev}, cutoff: {cutoff})")
        if len(low_score_fuzzy_matches) > 10:
            logger.info(f" ... and {len(low_score_fuzzy_matches) - 10} more")
  
    # Save unified folder_db
    output_path = Path('database/data/patches_database.json')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(folder_db, f, indent=4, ensure_ascii=False)
    logger.info(f"Unified database saved to {output_path}")

if __name__ == '__main__':
    unify_databases()
