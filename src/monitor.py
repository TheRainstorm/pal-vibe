import time
import os
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from src.logger import get_logger
from src.plugins import MoviePlugin, TVPlugin, WebDLPlugin
from src.database import VideoDatabase
from src.metadata import VIDEO_EXTENSIONS

logger = get_logger(__name__)

class BatchQueue:
    def __init__(self, process_callback, delete_callback, debounce_seconds=5):
        self.pending = {} # {filepath: {'ts': time, 'type': 'PROCESS'/'DELETE', 'config': ...}}
        self.lock = threading.Lock()
        self.debounce_seconds = debounce_seconds
        self.process_callback = process_callback
        self.delete_callback = delete_callback
        self.running = True

    def add(self, filepath, action_type, task_context):
        # Filter extensions for PROCESS
        if action_type == 'PROCESS':
            if not filepath.lower().endswith(VIDEO_EXTENSIONS):
                return

        with self.lock:
            self.pending[filepath] = {
                'ts': time.time(),
                'type': action_type,
                'ctx': task_context
            }
            logger.debug(f"Event queued: {action_type} {filepath}")

    def run(self):
        logger.info("Monitor worker thread started.")
        while self.running:
            time.sleep(1)
            now = time.time()
            to_process = []

            with self.lock:
                for filepath, data in list(self.pending.items()):
                    # DELETE: Immediate (1s delay)
                    # PROCESS: Debounce delay
                    delay = 1 if data['type'] == 'DELETE' else self.debounce_seconds
                    
                    if now - data['ts'] >= delay:
                        to_process.append((filepath, data))
                        del self.pending[filepath]

            for filepath, data in to_process:
                try:
                    if data['type'] == 'PROCESS':
                        self.process_callback(filepath, data['ctx'])
                    elif data['type'] == 'DELETE':
                        self.delete_callback(filepath, data['ctx'])
                except Exception as e:
                    logger.error(f"Error processing {filepath}: {e}", exc_info=True)

    def stop(self):
        self.running = False

class SrcHandler(FileSystemEventHandler):
    def __init__(self, queue, task_context):
        self.queue = queue
        self.ctx = task_context

    def on_created(self, event):
        if not event.is_directory:
            self.queue.add(event.src_path, 'PROCESS', self.ctx)

    def on_modified(self, event):
        if not event.is_directory:
            self.queue.add(event.src_path, 'PROCESS', self.ctx)

    def on_moved(self, event):
        if not event.is_directory:
            self.queue.add(event.dest_path, 'PROCESS', self.ctx)

class DstHandler(FileSystemEventHandler):
    def __init__(self, queue, task_context):
        self.queue = queue
        self.ctx = task_context

    def on_deleted(self, event):
        if not event.is_directory:
            self.queue.add(event.src_path, 'DELETE', self.ctx)

class MonitorManager:
    def __init__(self, db_cache):
        self.observer = Observer()
        self.db_cache = db_cache
        self.queue = BatchQueue(self.handle_process, self.handle_delete)
        self.worker_thread = threading.Thread(target=self.queue.run)
        self.plugins_cache = {} 

    def get_plugin(self, task_config, db):
        type_map = {
            0: TVPlugin, "tv": TVPlugin,
            1: MoviePlugin, "movie": MoviePlugin,
            2: WebDLPlugin, "webdl": WebDLPlugin
        }
        task_type = task_config.type
        if isinstance(task_type, str):
            task_type = task_type.lower()
        
        PluginClass = type_map.get(task_type)
        if not PluginClass: return None
        return PluginClass(db, task_config)

    def handle_process(self, filepath, ctx):
        logger.info(f"[Monitor] Processing: {filepath}")
        task_config = ctx['config']
        db = ctx['db']
        
        plugin = self.get_plugin(task_config, db)
        if plugin:
            plugin.process_file(filepath, task_config.src)

    def handle_delete(self, target_path, ctx):
        logger.info(f"[Monitor] Link deleted: {target_path}")
        db = ctx['db']
        
        src_filepath = db.get_file_by_target_path(target_path)
        
        if src_filepath:
            if os.path.exists(src_filepath):
                logger.warning(f"[Monitor] Deleting source file because link was deleted: {src_filepath}")
                try:
                    os.remove(src_filepath)
                    logger.info(f"[Monitor] Source file deleted.")
                except OSError as e:
                    logger.error(f"[Monitor] Failed to delete source file: {e}")
            else:
                logger.warning(f"[Monitor] Source file already gone: {src_filepath}")
            
            # Find source root to remove entry
            found_root = None
            if "roots" in db.data:
                for root in db.data["roots"]:
                    # Simple prefix check might be enough if paths are normalized
                    if src_filepath.startswith(root):
                        found_root = root
                        break
            
            if found_root:
                db.remove_entry(found_root, src_filepath)
                logger.info(f"[Monitor] DB entry removed.")
        else:
            logger.debug(f"[Monitor] No source file found for deleted link: {target_path}")

    def add_task(self, task_config, db):
        ctx = {'config': task_config, 'db': db}
        
        # Monitor Source
        monitor_src = getattr(task_config, 'monitor_src', True)
        if monitor_src:
            if os.path.exists(task_config.src):
                self.observer.schedule(SrcHandler(self.queue, ctx), path=task_config.src, recursive=True)
                logger.info(f"Monitoring Source: {task_config.src}")
            else:
                logger.error(f"Source path not found: {task_config.src}")
        else:
            logger.info(f"Monitoring Source DISABLED for: {task_config.src}")

        # Monitor Destination
        monitor_dst = getattr(task_config, 'monitor_dst', False)
        if monitor_dst:
            if os.path.exists(task_config.dst):
                self.observer.schedule(DstHandler(self.queue, ctx), path=task_config.dst, recursive=True)
                logger.info(f"Monitoring Destination: {task_config.dst}")
            else:
                try:
                    os.makedirs(task_config.dst, exist_ok=True)
                    self.observer.schedule(DstHandler(self.queue, ctx), path=task_config.dst, recursive=True)
                    logger.info(f"Monitoring Destination: {task_config.dst}")
                except OSError:
                    logger.error(f"Could not create/monitor destination: {task_config.dst}")
        else:
            logger.info(f"Monitoring Destination DISABLED for: {task_config.dst}")

    def start(self):
        self.worker_thread.start()
        self.observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        logger.info("Stopping monitor...")
        self.queue.stop()
        self.observer.stop()
        self.worker_thread.join()
        self.observer.join()