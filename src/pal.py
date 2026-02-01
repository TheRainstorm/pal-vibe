import os
import argparse
import yaml
from src.database import VideoDatabase
from src.metadata import scan_video_files
from src.link_manager import remove_link_and_empty_dirs
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin

class TaskConfig:
    """Helper class to convert dictionary to object with attributes, mimicking argparse.Namespace"""
    def __init__(self, **entries):
        self.__dict__.update(entries)

def get_db_instance(db_path, db_cache):
    # Ensure DB path is normalized
    db_path = os.path.normpath(db_path)
    if db_path not in db_cache:
        db_cache[db_path] = VideoDatabase(db_path)
    return db_cache[db_path]

def run_task(task_config, db_cache):
    # Ensure critical fields exist
    if not task_config.get("src") or not task_config.get("dst") or not task_config.get("type"):
        print(f"Skipping invalid task config: {task_config}")
        return

    # Normalize paths
    task_config["src"] = os.path.normpath(task_config["src"])
    task_config["dst"] = os.path.normpath(task_config["dst"])
    
    # Determine DB path
    db_path = task_config.get("db", "pal_database.yaml")
    db = get_db_instance(db_path, db_cache)

    # Convert config dict to object for Plugins
    args = TaskConfig(**task_config)

    # Select Plugin
    # Support both int (legacy) and string types
    type_map = {
        0: TVPlugin, "tv": TVPlugin,
        1: MoviePlugin, "movie": MoviePlugin,
        2: WebDLPlugin, "webdl": WebDLPlugin
    }
    
    # Handle type being case-insensitive string
    task_type = task_config["type"]
    if isinstance(task_type, str):
        task_type = task_type.lower()
        
    PluginClass = type_map.get(task_type)
    if not PluginClass:
        print(f"Unknown type: {task_type}")
        return

    plugin = PluginClass(db, args)
    source_root = task_config["src"]

    print(f"--- Running Task ---")
    print(f"Source: {source_root}")
    print(f"Type: {plugin.get_type_name()}")
    print(f"Database: {db_path}")

    found_files = scan_video_files(source_root)
    current_files_set = set(found_files)

    # Cleanup: Only check files belonging to this source_root
    # This prevents deleting links from other tasks sharing the same DB
    # stored_files are now absolute paths
    stored_files_in_root = db.get_files_by_source_root(source_root)
    
    for stored_file in stored_files_in_root:
        if stored_file not in current_files_set and not os.path.exists(stored_file):
            print(f"File removed: {stored_file}, cleaning up...")
            entry = db.get_video_entry(source_root, stored_file)
            if entry and entry.get("target_path"):
                remove_link_and_empty_dirs(entry["target_path"])
            db.remove_entry(source_root, stored_file)

    if not found_files:
        print("No video files found.")

    for filepath in found_files:
        plugin.process_file(filepath, source_root)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and link video files for Jellyfin.")
    
    # Mode selection
    parser.add_argument("-c", "--config", help="Path to YAML configuration file.")
    
    # Single task arguments (Legacy/Direct mode)
    parser.add_argument("-s", "--src", help="Source directory to scan video files.")
    parser.add_argument("-d", "--dst", help="Destination directory for linked files.")
    parser.add_argument("-t", "--type", help="Type of video: 0/tv, 1/movie, 2/webdl.")
    parser.add_argument("-S", "--soft-link", action="store_true", help="Create soft links.")
    parser.add_argument("--movie-folder", default="Movie", help="Subfolder for movies.")
    parser.add_argument("--tv-folder", default="TV", help="Subfolder for TV series.")
    parser.add_argument("--db", default="pal_database.yaml", help="Database file path.")
    
    # LLM arguments
    parser.add_argument("--use-llm", action="store_true", help="Use LLM for metadata.")
    parser.add_argument("--llm-api-key", help="API key for LLM.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1", help="LLM API Base URL.")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo", help="LLM Model name.")

    args = parser.parse_args()
    
    db_cache = {}

    if args.config:
        if not os.path.exists(args.config):
            print(f"Config file not found: {args.config}")
            exit(1)
            
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
            
        defaults = config.get("defaults", {})
        tasks = config.get("tasks", [])
        
        for task in tasks:
            # Merge defaults with task config
            merged_task = defaults.copy()
            merged_task.update(task)
            run_task(merged_task, db_cache)
            
    else:
        # Run in single task mode using CLI args
        if not args.src or not args.dst or not args.type:
            parser.error("src, dst, and type are required unless -c/--config is used.")
            
        # Convert args namespace to dict for consistency
        task_config = vars(args)
        
        # Handle type conversion if it's a digit string
        if isinstance(task_config["type"], str) and task_config["type"].isdigit():
            task_config["type"] = int(task_config["type"])
            
        run_task(task_config, db_cache)