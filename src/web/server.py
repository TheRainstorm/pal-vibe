from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import os
import yaml
import threading

# Import project modules
from src.pal import load_configuration, prepare_and_check_task, get_db_instance, TaskConfig
from src.database import VideoDatabase
from src.logger import setup_logging, get_logger
from src.metadata import scan_video_files

# Setup logging
setup_logging(verbose=True)
logger = get_logger(__name__)

app = FastAPI(title="PAL Vibe Web UI")

app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

@app.get("/")
async def read_root():
    return RedirectResponse(url="/static/index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- State ---
class AppState:
    def __init__(self):
        self.config_path = "config.yaml"
        self.db_cache = {}
        self.tasks = [] # List of merged task configs (dicts)
        self.global_providers = {}

state = AppState()

# --- Models ---
class TaskInfo(BaseModel):
    id: int
    src: str
    dst: str
    type: str
    db: str

class FileNode(BaseModel):
    id: str
    name: str
    path: str # Relative path for display
    full_path: str # Absolute path for ID
    type: str # 'file' or 'folder'
    children: Optional[List['FileNode']] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status: Optional[str] = None # 'linked', 'missing', 'error', 'scanned'
    # Extra for DST view
    source_path: Optional[str] = None

class StatsResponse(BaseModel):
    src_files_count: int
    dst_files_count: int
    error_files_count: int
    tasks_count: int

class MetadataUpdate(BaseModel):
    full_path: str
    source_root: str
    metadata: Dict[str, Any]

class BatchMetadataUpdate(BaseModel):
    updates: List[MetadataUpdate]

# --- Helpers ---
def build_file_tree_from_paths(paths: List[str], root_path: str, db: VideoDatabase) -> List[FileNode]:
    tree = []
    # Convert to relative paths
    rel_paths = []
    for p in paths:
        try:
            rel = os.path.relpath(p, root_path)
            rel_paths.append((rel, p))
        except ValueError:
            continue
    
    rel_paths.sort()

    for rel_path, full_path in rel_paths:
        parts = rel_path.split(os.sep)
        filename = parts[-1]
        
        # Ensure parent dirs exist in tree
        current_rel_parent = ""
        current_children = tree
        
        for part in parts[:-1]:
            current_rel_parent = os.path.join(current_rel_parent, part) if current_rel_parent else part
            
            # Check if this dir node exists
            found = False
            for node in current_children:
                if node.name == part and node.type == 'folder':
                    current_children = node.children
                    found = True
                    break
            
            if not found:
                new_node = FileNode(
                    id=os.path.join(root_path, current_rel_parent),
                    name=part,
                    path=current_rel_parent,
                    full_path=os.path.join(root_path, current_rel_parent),
                    type='folder',
                    children=[]
                )
                current_children.append(new_node)
                current_children = new_node.children

        # Create File Node
        entry = db.get_video_entry(root_path, full_path)
        meta = entry.get("metadata") if entry else None
        err = entry.get("error") if entry else None
        
        status = "scanned"
        if err: status = "error"
        elif entry and entry.get("target_path") and os.path.exists(entry["target_path"]): status = "linked"
        elif entry: status = "missing_link"
        
        node = FileNode(
            id=full_path,
            name=filename,
            path=rel_path,
            full_path=full_path,
            type='file',
            metadata=meta,
            error=err,
            status=status
        )
        current_children.append(node)

    return tree

def build_dst_tree() -> List[FileNode]:
    """
    Reconstructs the destination file tree based on all DB entries.
    Builds tree relative to each Task's configured DST directory.
    """
    tree = []
    
    # Collect all valid target files
    file_list = []
    
    for task in state.tasks:
        db_path = task.get("db", "pal_database.yaml")
        db = state.db_cache.get(os.path.normpath(db_path))
        src_root = task.get("src")
        task_dst = os.path.normpath(task.get("dst"))
        
        if db and src_root:
            files = db.get_files_by_source_root(src_root)
            for f in files:
                entry = db.get_video_entry(src_root, f)
                target = entry.get("target_path")
                if target and os.path.isabs(target):
                    file_list.append({
                        "target": target,
                        "source": f,
                        "metadata": entry.get("metadata"),
                        "error": entry.get("error"),
                        "task_dst": task_dst
                    })

    # Sort to ensure consistent order
    file_list.sort(key=lambda x: x["target"])

    def insert_node(root_list, path_parts, file_data):
        current_level = root_list
        current_rel_path = ""
        
        for i, part in enumerate(path_parts):
            is_file = (i == len(path_parts) - 1)
            current_rel_path = os.path.join(current_rel_path, part)
            
            # Find existing node
            found_node = None
            for node in current_level:
                if node.name == part and node.type == ('file' if is_file else 'folder'):
                    found_node = node
                    break
            
            if not found_node:
                if is_file:
                    new_node = FileNode(
                        id=file_data["target"],
                        name=part,
                        path=current_rel_path,
                        full_path=file_data["target"],
                        type='file',
                        metadata=file_data["metadata"],
                        error=file_data["error"],
                        status="linked",
                        source_path=file_data["source"]
                    )
                else:
                    new_node = FileNode(
                        id=f"folder:{current_rel_path}", 
                        name=part,
                        path=current_rel_path,
                        full_path=current_rel_path, 
                        type='folder',
                        children=[]
                    )
                current_level.append(new_node)
                found_node = new_node
            
            if not is_file:
                current_level = found_node.children

    for item in file_list:
        try:
            # Calculate relative path from the Task's DST root
            rel = os.path.relpath(item["target"], item["task_dst"])
            if rel.startswith(".."): 
                continue
            
            parts = rel.split(os.sep)
            insert_node(tree, parts, item)
        except ValueError:
            pass

    return tree

# --- Endpoints ---

@app.on_event("startup")
async def startup_event():
    await reload_all()

@app.post("/api/reload")
async def reload_all():
    """Reload config and all databases from disk."""
    logger.info("Reloading configuration and databases...")
    
    # Reload Config
    if os.path.exists(state.config_path):
        providers, tasks = load_configuration(state.config_path)
        state.tasks = tasks
        state.global_providers = providers
    else:
        logger.warning("No config.yaml found.")
        state.tasks = []

    # Reload DBs
    # Re-initialize cache based on new tasks, but also reload existing ones
    for task in state.tasks:
        db_path = task.get("db", "pal_database.yaml")
        get_db_instance(db_path, state.db_cache)
        
    for db in state.db_cache.values():
        db.reload()
        
    logger.info("Reload complete.")
    return {"status": "reloaded", "tasks": len(state.tasks)}

@app.get("/api/tasks", response_model=List[TaskInfo])
async def get_tasks():
    res = []
    for i, t in enumerate(state.tasks):
        res.append(TaskInfo(
            id=i,
            src=t.get("src", ""),
            dst=t.get("dst", ""),
            type=str(t.get("type", "")),
            db=t.get("db", "pal_database.yaml")
        ))
    return res

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    src_count = 0
    dst_count = 0
    error_count = 0
    
    for task in state.tasks:
        src_root = task.get("src")
        db_path = task.get("db", "pal_database.yaml")
        db = state.db_cache.get(os.path.normpath(db_path))
        
        if db and src_root:
            files = db.get_files_by_source_root(src_root)
            src_count += len(files)
            for f in files:
                entry = db.get_video_entry(src_root, f)
                if entry:
                    if entry.get("error"): error_count += 1
                    if entry.get("target_path") and os.path.exists(entry["target_path"]):
                        dst_count += 1
    
    return StatsResponse(
        src_files_count=src_count,
        dst_files_count=dst_count,
        error_files_count=error_count,
        tasks_count=len(state.tasks)
    )

@app.get("/api/tasks/{task_id}/tree")
async def get_task_tree(task_id: int):
    if task_id >= len(state.tasks) or task_id < 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = state.tasks[task_id]
    src_root = task.get("src")
    db_path = task.get("db", "pal_database.yaml")
    db = state.db_cache.get(os.path.normpath(db_path))
    
    if not db or not src_root:
        return []
    
    files = db.get_files_by_source_root(src_root)
    return build_file_tree_from_paths(files, src_root, db)

@app.get("/api/dst/tree")
async def get_dst_tree():
    return build_dst_tree()

@app.get("/api/errors")
async def get_errors():
    errors = []
    for task in state.tasks:
        src_root = task.get("src")
        db_path = task.get("db", "pal_database.yaml")
        db = state.db_cache.get(os.path.normpath(db_path))
        
        if db and src_root:
            files = db.get_files_by_source_root(src_root)
            for f in files:
                entry = db.get_video_entry(src_root, f)
                if entry and entry.get("error"):
                    errors.append(FileNode(
                        id=f,
                        name=os.path.basename(f),
                        path=f, # Show absolute path for errors
                        full_path=f,
                        type='file',
                        metadata=entry.get("metadata"),
                        error=entry.get("error"),
                        status="error"
                    ))
    return errors

@app.post("/api/scan/{task_id}")
async def trigger_scan(task_id: int):
    if task_id >= len(state.tasks) or task_id < 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_config = state.tasks[task_id]
    
    try:
        result = prepare_and_check_task(task_config, state.db_cache, state.global_providers)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to prepare task")
            
        args, db, PluginClass = result
        plugin = PluginClass(db, args)
        
        logger.info(f"Manual scan triggered for task {task_id}")
        found_files = scan_video_files(args.src)
        
        # Cleanup
        stored_files = db.get_files_by_source_root(args.src)
        found_set = set(found_files)
        for sf in stored_files:
            if sf not in found_set and not os.path.exists(sf):
                entry = db.get_video_entry(args.src, sf)
                if entry and entry.get("target_path"):
                    from src.link_manager import remove_link_and_empty_dirs
                    remove_link_and_empty_dirs(entry["target_path"])
                db.remove_entry(args.src, sf)
        
        # Process using new batch logic
        plugin.process_files(found_files, args.src)
            
        return {"status": "success", "files_scanned": len(found_files)}
        
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

async def _process_metadata_update(update: MetadataUpdate):
    # Find which task owns this file
    task_config = None
    for task in state.tasks:
        if task.get("src") == update.source_root:
            task_config = task
            break
            
    if not task_config:
        for task in state.tasks:
            if update.full_path.startswith(task.get("src")):
                task_config = task
                update.source_root = task.get("src") 
                break
    
    if not task_config:
        raise Exception(f"Source root configuration not found for {update.full_path}")

    db_path = task_config.get("db", "pal_database.yaml")
    db = state.db_cache.get(os.path.normpath(db_path))
    
    if not db:
        raise Exception("Database not loaded")

    entry = db.get_video_entry(update.source_root, update.full_path)

    # Instantiate plugin
    result = prepare_and_check_task(task_config, state.db_cache, state.global_providers)
    if not result:
        raise Exception("Plugin load failed")
    
    args, _, PluginClass = result
    plugin = PluginClass(db, args)
    
    old_target = entry.get("target_path") if entry else None
    old_hash = entry.get("metadata_hash")
    if plugin._check_and_fix_metadata(update.metadata):
        logger.info(f"Fixed metadata types for {update.full_path}")
    
    logger.debug(f"Updating metadata for {update.full_path}: {update.metadata}")
    db.update_video_entry(
        update.source_root, 
        args.dst, 
        update.full_path, 
        update.metadata, 
        old_target, 
        old_hash, 
        error=None 
    )
    
    plugin.process_file(update.full_path, update.source_root)

@app.post("/api/file/update")
async def update_metadata(update: MetadataUpdate):
    try:
        await _process_metadata_update(update)
        return {"status": "updated"}
    except Exception as e:
        logger.error(f"Update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/files/batch_update")
async def batch_update_metadata(batch: BatchMetadataUpdate):
    count = 0
    errors = []
    for update in batch.updates:
        try:
            await _process_metadata_update(update)
            count += 1
        except Exception as e:
            errors.append(f"{os.path.basename(update.full_path)}: {str(e)}")
    
    if errors:
        return {"status": "partial_success", "updated": count, "errors": errors}
    return {"status": "success", "updated": count}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
