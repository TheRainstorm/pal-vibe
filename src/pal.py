import os
import argparse
import yaml
from src.database import VideoDatabase
from src.metadata import scan_video_files
from src.link_manager import remove_link_and_empty_dirs
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin
from src.logger import setup_logging, get_logger

logger = get_logger(__name__)

class TaskConfig:
    """Helper class to convert dictionary to object with attributes."""
    def __init__(self, **entries):
        self.__dict__.update(entries)

def get_db_instance(db_path, db_cache):
    db_path = os.path.normpath(db_path)
    if db_path not in db_cache:
        logger.debug(f"Initializing database instance: {db_path}")
        db_cache[db_path] = VideoDatabase(db_path)
    return db_cache[db_path]

def run_task(task_config, db_cache, global_providers):
    if not task_config.get("src") or not task_config.get("dst") or not task_config.get("type"):
        logger.warning(f"Skipping invalid task config: {task_config}")
        return

    task_config["src"] = os.path.normpath(task_config["src"])
    task_config["dst"] = os.path.normpath(task_config["dst"])
    
    db_path = task_config.get("db", "pal_database.yaml")
    db = get_db_instance(db_path, db_cache)

    if "providers" not in task_config:
        task_config["providers"] = global_providers

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
        logger.error(f"Unknown task type: {task_type}")
        return

    plugin = PluginClass(db, args)
    source_root = task_config["src"]

    logger.info(f"--- Running Task ---")
    logger.info(f"Source: {source_root}")
    logger.info(f"Type: {plugin.get_type_name()}")
    logger.info(f"Chain: {task_config['chain']}")
    logger.info(f"Database: {db.db_path}")

    found_files = scan_video_files(source_root)
    logger.info(f"Found {len(found_files)} files in source directory.")
    current_files_set = set(found_files)

    stored_files_in_root = db.get_files_by_source_root(source_root)
    
    removed_count = 0
    for stored_file in stored_files_in_root:
        if stored_file not in current_files_set and not os.path.exists(stored_file):
            logger.info(f"File removed: {stored_file}, cleaning up...")
            entry = db.get_video_entry(source_root, stored_file)
            if entry and entry.get("target_path"):
                remove_link_and_empty_dirs(entry["target_path"])
            db.remove_entry(source_root, stored_file)
            removed_count += 1
    
    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} removed files.")

    if not found_files:
        logger.warning("No video files found in source directory.")

    for filepath in found_files:
        plugin.process_file(filepath, source_root)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and link video files for Jellyfin.")
    
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging (DEBUG level).")
    parser.add_argument("-c", "--config", help="Path to YAML configuration file.")
    
    parser.add_argument("-s", "--src", help="Source directory.")
    parser.add_argument("-d", "--dst", help="Destination directory.")
    parser.add_argument("-t", "--type", help="Type of video: movie, tv, webdl.")
    parser.add_argument("-S", "--soft-link", action="store_true", help="Create soft links.")
    parser.add_argument("--sub-folder", help="Subfolder name within destination (default depends on type).")
    parser.add_argument("--db", default="pal_database.yaml", help="Database file path.")
    
    parser.add_argument("--llm-api-key", help="API key for CLI LLM.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1", help="LLM API Base URL.")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo", help="LLM Model name.")
    parser.add_argument("--chain", help="Comma separated processing chain.")
    parser.add_argument("--log-level", help="Set logging level (DEBUG, INFO, WARNING, ERROR).")

    args = parser.parse_args()
    
    setup_logging(args.verbose, args.log_level)
    
    db_cache = {}
    global_providers = {}

    if args.config:
        if not os.path.exists(args.config):
            logger.error(f"Config file not found: {args.config}")
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
        
        if isinstance(task_config["type"], str) and task_config["type"].isdigit():
            task_config["type"] = int(task_config["type"])

        if args.llm_api_key:
            global_providers["cli_llm"] = {
                "type": "llm",
                "api_key": args.llm_api_key,
                "base_url": args.llm_api_base,
                "model": args.llm_model
            }
        
        if args.chain:
            task_config["chain"] = args.chain.split(',')
        elif args.llm_api_key:
            task_config["chain"] = ["guessit", "cli_llm"]
        else:
            task_config["chain"] = ["guessit"]

        run_task(task_config, db_cache, global_providers)