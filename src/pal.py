import os
import argparse
from src.database import VideoDatabase
from src.metadata import scan_video_files
from src.link_manager import remove_link_and_empty_dirs
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and link video files for Jellyfin.")
    parser.add_argument("-s", "--src", required=True, help="Source directory to scan video files.")
    parser.add_argument("-d", "--dst", required=True, help="Destination directory for linked files.")
    parser.add_argument("-t", "--type", type=int, choices=[0, 1, 2], required=True,
                        help="Type of video: 0 for TV, 1 for Movie, 2 for WebDL.")
    parser.add_argument("-S", "--soft-link", action="store_true",
                        help="Create soft links instead of hard links.")
    parser.add_argument("--movie-folder", default="Movie",
                        help="Subfolder name for movies within the destination directory (default: Movie).")
    parser.add_argument("--tv-folder", default="TV",
                        help="Subfolder name for TV series within the destination directory (default: TV).")
    parser.add_argument("--db", default="pal_database.yaml",
                        help="Path to the YAML database file (default: pal_database.yaml).")
    # LLM arguments
    parser.add_argument("--use-llm", action="store_true",
                        help="Use an LLM (OpenAI compatible API) for metadata extraction.")
    parser.add_argument("--llm-api-key", help="API key for the LLM service. Required if --use-llm is true.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1",
                        help="Base URL for the OpenAI-compatible API (default: https://api.openai.com/v1).")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo",
                        help="Model name to use for LLM metadata extraction (default: gpt-3.5-turbo).")

    args = parser.parse_args()

    if args.use_llm and not args.llm_api_key:
        parser.error("--llm-api-key is required when --use-llm is enabled.")

    db = VideoDatabase(args.db)

    # Select Plugin
    plugin_map = {
        0: TVPlugin,
        1: MoviePlugin,
        2: WebDLPlugin
    }
    PluginClass = plugin_map.get(args.type)
    plugin = PluginClass(db, args)

    print(f"Scanning for video files in {args.src} with type {plugin.get_type_name()}...")
    found_files = scan_video_files(args.src)

    # Core Demand 3: Check for orphaned links and remove them
    # Clean up DB for removed files
    current_files_set = set(found_files)
    stored_files = db.get_all_files()
    for stored_file in stored_files:
        if stored_file not in current_files_set and not os.path.exists(stored_file):
            print(f"File removed: {stored_file}, cleaning up...")
            entry = db.get_video_entry(stored_file)
            if entry and "target_path" in entry:
                remove_link_and_empty_dirs(entry["target_path"])
            db.remove_entry(stored_file)

    if not found_files:
        print("No video files found.")

    for filepath in found_files:
        plugin.process_file(filepath)
