import json
import os
from pathlib import Path

# --- Configuration ---
# Assuming the JSON file is located at 'database/data/patches_database.json'
# relative to where you run this script.
DATABASE_PATH = Path('database') / 'data' / 'patches_database.json'
# ---------------------

def minify_json_database(file_path: Path):
    """
    Loads a JSON file, strips all unnecessary whitespace, and saves it.
    """
    if not file_path.exists():
        print(f"Error: Database file not found at '{file_path}'")
        return

    print(f"Attempting to read file: {file_path}")
    
    # 1. Load the JSON data
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_size = os.path.getsize(file_path)
        print(f"Original size: {original_size} bytes")

    except json.JSONDecodeError:
        print("Error: Failed to parse JSON. Check file format.")
        return
    except Exception as e:
        print(f"An unexpected error occurred during read: {e}")
        return

    # 2. Write the JSON data back, minified
    # The key to minification is using separators=(',', ':')
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, separators=(',', ':'))

        minified_size = os.path.getsize(file_path)
        reduction = original_size - minified_size
        percentage = (reduction / original_size) * 100 if original_size else 0
        
        print("\n--- Minification Successful ---")
        print(f"Minified size: {minified_size} bytes")
        print(f"Size Reduction: {reduction} bytes ({percentage:.2f}%)")
        print(f"File overwritten at: {file_path}")
        
    except Exception as e:
        print(f"An unexpected error occurred during write: {e}")


if __name__ == "__main__":
    minify_json_database(DATABASE_PATH)