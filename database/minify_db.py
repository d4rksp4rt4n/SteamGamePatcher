import json
import os
from pathlib import Path

# --- Configuration ---
INPUT_PATH = Path('database') / 'data' / 'patches_data.json'
OUTPUT_PATH = Path('database') / 'data' / 'patches_database.json'
# ---------------------

def minify_json_database(input_path: Path, output_path: Path):
    """
    Loads patches_data.json, minifies it, and saves it as patches_database.json
    """
    if not input_path.exists():
        print(f"❌ Error: Input file not found at '{input_path}'")
        print("   Make sure the fetch step completed successfully.")
        return

    print(f"📥 Reading: {input_path}")

    # 1. Load the JSON data
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_size = os.path.getsize(input_path)
        print(f"   Original size: {original_size:,} bytes")
    except json.JSONDecodeError as e:
        print(f"❌ Error: Failed to parse JSON: {e}")
        return
    except Exception as e:
        print(f"❌ Unexpected error reading file: {e}")
        return

    # 2. Minify and save to patches_database.json
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
        
        minified_size = os.path.getsize(output_path)
        reduction = original_size - minified_size
        percentage = (reduction / original_size) * 100 if original_size else 0
        
        print("\n✅ Minification Successful")
        print(f"   Minified size : {minified_size:,} bytes")
        print(f"   Size reduction: {reduction:,} bytes ({percentage:.2f}%)")
        print(f"   Saved as      : {output_path}")
        
    except Exception as e:
        print(f"❌ Error writing minified file: {e}")

if __name__ == "__main__":
    minify_json_database(INPUT_PATH, OUTPUT_PATH)
