import os
import argparse
import yaml
from src.database import VideoDatabase
from src.metadata import scan_video_files
from src.link_manager import remove_link_and_empty_dirs
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin

class TaskConfig:
    """Helper class to convert dictionary to object with attributes."""
    def __init__(self, **entries):
        self.__dict__.update(entries)

def get_db_instance(db_path, db_cache):
    db_path = os.path.normpath(db_path)
    if db_path not in db_cache:
        db_cache[db_path] = VideoDatabase(db_path)
    return db_cache[db_path]

def run_task(task_config, db_cache, global_providers):
    if not task_config.get("src") or not task_config.get("dst") or not task_config.get("type"):
        print(f"Skipping invalid task config: {task_config}")
        return

    task_config["src"] = os.path.normpath(task_config["src"])
    task_config["dst"] = os.path.normpath(task_config["dst"])
    
    db_path = task_config.get("db", "pal_database.yaml")
    db = get_db_instance(db_path, db_cache)

    # Inject global providers into task config if not present
    if "providers" not in task_config:
        task_config["providers"] = global_providers

    # Default chain if not specified
    if "chain" not in task_config:
        task_config["chain"] = ["guessit"]

    args = TaskConfig(**task_config)

    type_map = {
        0: TVPlugin, "tv": TVPlugin,
        1: MoviePlugin, "movie": MoviePlugin,
        2: WebDLPlugin, "webdl": WebDLPlugin
    }
    
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
    print(f"Chain: {task_config['chain']}")
    print(f"Database: {db.db_path}")

    found_files = scan_video_files(source_root)
    current_files_set = set(found_files)

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
    
    parser.add_argument("-c", "--config", help="Path to YAML configuration file.")
    
    # Single task arguments
    parser.add_argument("-s", "--src", help="Source directory.")
    parser.add_argument("-d", "--dst", help="Destination directory.")
    parser.add_argument("-t", "--type", help="Type of video: movie, tv, webdl.")
    parser.add_argument("-S", "--soft-link", action="store_true", help="Create soft links.")
    parser.add_argument("--movie-folder", default="Movie", help="Subfolder for movies.")
    parser.add_argument("--tv-folder", default="TV", help="Subfolder for TV series.")
    parser.add_argument("--db", default="pal_database.yaml", help="Database file path.")
    
    # CLI LLM args (packaged into a temporary provider)
    parser.add_argument("--llm-api-key", help="API key for CLI LLM.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1", help="LLM API Base URL.")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo", help="LLM Model name.")
    parser.add_argument("--chain", help="Comma separated processing chain (e.g. guessit,cli_llm). Default: guessit")

    args = parser.parse_args()
    
    db_cache = {}
    global_providers = {}

    if args.config:
        if not os.path.exists(args.config):
            print(f"Config file not found: {args.config}")
            exit(1)
            
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
            
        defaults = config.get("defaults", {})
        global_providers = config.get("providers", {})
        tasks = config.get("tasks", [])
        
        for task in tasks:
            merged_task = defaults.copy()
            merged_task.update(task)
            run_task(merged_task, db_cache, global_providers)
            
    else:
        if not args.src or not args.dst or not args.type:
            parser.error("src, dst, and type are required unless -c/--config is used.")
            
        task_config = vars(args)
        
        # Handle type conversion
        if isinstance(task_config["type"], str) and task_config["type"].isdigit():
            task_config["type"] = int(task_config["type"])

        # Setup CLI provider if API key is present
        if args.llm_api_key:
            global_providers["cli_llm"] = {
                "type": "llm",
                "api_key": args.llm_api_key,
                "base_url": args.llm_api_base,
                "model": args.llm_model
            }
        
        # Parse chain arg
        if args.chain:
            task_config["chain"] = args.chain.split(',')
        elif args.llm_api_key:
            # Smart default if key provided but no chain
            task_config["chain"] = ["guessit", "cli_llm"]
        else:
            task_config["chain"] = ["guessit"]

        run_task(task_config, db_cache, global_providers)
