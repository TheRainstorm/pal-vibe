import os
import argparse
import yaml
from src.database import VideoDatabase
from src.metadata import scan_video_files
from src.link_manager import remove_link_and_empty_dirs
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin
from src.logger import setup_logging, get_logger
from src.monitor import MonitorManager

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

def get_global_defaults():
    # config file defaults
    return {
        "db": "pal_database.yaml",
        "soft_link": True,
        "min_file_size": 100,
        "batch_size": 26,
        "retry_failed": False,
        "monitor_src": False,
        "monitor_dst": False,
        "chain": ["guessit"]
    }

def load_configuration(config_path):
    if not os.path.exists(config_path):
        logger.error(f"Config file not found: {config_path}")
        exit(1)
        
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    defaults = config.get("defaults", {})
    providers = config.get("providers", {})
    raw_tasks = config.get("tasks", [])
    
    tasks = []
    for task in raw_tasks:
        merged_task = get_global_defaults()
        merged_task.update(defaults)
        merged_task.update(task)
        tasks.append(merged_task)
    return providers, tasks

def prepare_and_check_task(task_config, db_cache, global_providers):
    if not task_config.get("src") or not task_config.get("dst") or not task_config.get("type"):
        logger.warning(f"Skipping invalid task config: {task_config}")
        return None

    task_config["src"] = os.path.normpath(task_config["src"])
    task_config["dst"] = os.path.normpath(task_config["dst"])
    
    db_path = task_config.get("db", "pal_database.yaml")
    db = get_db_instance(db_path, db_cache)

    if "providers" not in task_config:
        task_config["providers"] = global_providers

    # check global_defaults keys exist
    for key, _ in get_global_defaults().items():
        if key not in task_config:
            logger.error(f"Missing required task config key: {key}")
            return None
    
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
        return None

    return args, db, PluginClass

def cleanup_removed_files(db, source_root, current_files_set):
    """
    Checks for files in DB that no longer exist on disk and removes them.
    """
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

def run_task_once(task_config, db_cache, global_providers):
    result = prepare_and_check_task(task_config, db_cache, global_providers)
    if not result: return

    args, db, PluginClass = result
    plugin = PluginClass(db, args)
    source_root = args.src

    logger.info(f"--- Running Task ---")
    logger.info(f"Source: {source_root}")
    logger.info(f"Type: {plugin.get_type_name()}")
    logger.info(f"Chain: {args.chain}")
    logger.info(f"Database: {db.db_path}")

    min_size = getattr(args, "min_file_size", 100)
    found_files = scan_video_files(source_root, min_size_mb=min_size)
    logger.info(f"Found {len(found_files)} files in source directory (min size: {min_size}MB).")
    
    # Cleanup logic encapsulated
    cleanup_removed_files(db, source_root, set(found_files))

    if not found_files:
        logger.warning("No video files found in source directory.")

    plugin.process_files(found_files, source_root)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and link video files for Jellyfin.")
    
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging (DEBUG level).")
    parser.add_argument("-c", "--config", help="Path to YAML configuration file.")
    parser.add_argument("--monitor", action="store_true", help="Run in monitoring mode.")
    
    parser.add_argument("-s", "--src", help="Source directory.")
    parser.add_argument("-d", "--dst", help="Destination directory.")
    parser.add_argument("-t", "--type", help="Type of video: movie, tv, webdl.")
    parser.add_argument("-S", "--soft-link", action="store_true", help="Create soft links.")
    parser.add_argument("--sub-folder", help="Subfolder name within destination.")
    parser.add_argument("--db", default="pal_database.yaml", help="Database file path.")
    parser.add_argument("--batch-size", type=int, default=26, help="Batch size for metadata extraction.")
    parser.add_argument("--min-file-size", type=int, default=100, help="Minimum file size in MB (default 100).")
    parser.add_argument("--retry-failed", action="store_true", help="Retry files previously marked as errors even if hash matches.")
    
    parser.add_argument("--chain", help="Comma separated processing chain.")
    parser.add_argument("--llm-api-key", help="API key for CLI LLM.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1", help="LLM API Base URL.")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo", help="LLM Model name.")
    parser.add_argument("--log-level", help="Set logging level (DEBUG, INFO, WARNING, ERROR).")

    args = parser.parse_args()
    
    setup_logging(args.verbose, args.log_level)
    
    db_cache = {}
    global_providers = {}
    global_defaults = get_global_defaults()
    
    tasks_to_run = []

    if args.config:
        providers, tasks = load_configuration(args.config)
        global_providers.update(providers)
        tasks_to_run.extend(tasks)
    else:
        if not args.src or not args.dst or not args.type:
            parser.error("src, dst, and type are required unless -c/--config is used.")
        
        task_config = global_defaults.copy()
        task_config.update(vars(args))
        
        if args.llm_api_key:
            global_providers["cli_llm"] = {
                "type": "llm",
                "api_key": args.llm_api_key,
                "base_url": args.llm_api_base,
                "model": args.llm_model
            }
        # fix cli chain parsing
        if args.chain:
            task_config["chain"] = args.chain.split(',')

        tasks_to_run.append(task_config)

    if not tasks_to_run:
        logger.error("No tasks configured.")
        exit(1)

    if args.monitor:
        logger.info("Starting Monitor Mode...")
        manager = MonitorManager(db_cache)
        
        for task_config in tasks_to_run:
            result = prepare_and_check_task(task_config, db_cache, global_providers)
            if result:
                task_args, db, _ = result
                manager.add_task(task_args, db)
        
        manager.start() 
    else:
        for task_config in tasks_to_run:
            run_task_once(task_config, db_cache, global_providers)
