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
from src.pal import load_configuration, prepare_task, get_db_instance, TaskConfig
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
    Grouping by Task is hard because multiple tasks might output to same DST structure.
    So we build a unified tree based on target_paths.
    """
    tree = []
    
    # Collect all valid target paths from all DBs
    # Map target_path -> (source_path, metadata, error, db_instance)
    file_map = {} 
    
    for task in state.tasks:
        db_path = task.get("db", "pal_database.yaml")
        db = state.db_cache.get(os.path.normpath(db_path))
        src_root = task.get("src")
        
        if db and src_root:
            files = db.get_files_by_source_root(src_root)
            for f in files:
                entry = db.get_video_entry(src_root, f)
                target = entry.get("target_path")
                if target:
                    file_map[target] = {
                        "source_path": f,
                        "metadata": entry.get("metadata"),
                        "error": entry.get("error")
                    }

    # Sort keys
    sorted_targets = sorted(file_map.keys())
    
    # We need a common root to make relative paths? 
    # Or just assume absolute paths and build tree from root?
    # Since dst paths might be on different drives, we can't easily relpath.
    # But usually they share a common prefix if config is sane.
    # Let's verify common prefix.
    if not sorted_targets:
        return []
        
    common_prefix = os.path.commonpath(sorted_targets)
    # If common prefix is file system root or empty, tree might be messy.
    # But let's use it as base.
    if os.path.isfile(common_prefix): # Single file case
        common_prefix = os.path.dirname(common_prefix)

    for full_target in sorted_targets:
        data = file_map[full_target]
        try:
            rel_path = os.path.relpath(full_target, common_prefix)
        except ValueError:
            # Different drive? Just show full path as name at root level
            rel_path = full_target
            
        parts = rel_path.split(os.sep)
        filename = parts[-1]
        
        current_children = tree
        current_full_path_dir = common_prefix
        
        for part in parts[:-1]:
            current_full_path_dir = os.path.join(current_full_path_dir, part)
            
            found = False
            for node in current_children:
                if node.name == part and node.type == 'folder':
                    current_children = node.children
                    found = True
                    break
            
            if not found:
                new_node = FileNode(
                    id=current_full_path_dir,
                    name=part,
                    path=part, # Just name
                    full_path=current_full_path_dir,
                    type='folder',
                    children=[]
                )
                current_children.append(new_node)
                current_children = new_node.children
        
        node = FileNode(
            id=full_target,
            name=filename,
            path=rel_path,
            full_path=full_target,
            type='file',
            metadata=data["metadata"],
            error=data["error"],
            status="linked", # If it's in target map, it's theoretically linked
            source_path=data["source_path"]
        )
        current_children.append(node)
        
    return tree

# --- Endpoints ---

@app.on_event("startup")
async def startup_event():
    if os.path.exists(state.config_path):
        defaults, providers, tasks = load_configuration(state.config_path)
        state.tasks = tasks
        state.global_providers = providers
        # Pre-load DBs
        for task in tasks:
            db_path = task.get("db", "pal_database.yaml")
            get_db_instance(db_path, state.db_cache)
        logger.info(f"Loaded {len(state.tasks)} tasks.")
    else:
        logger.warning("No config.yaml found.")

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
        result = prepare_task(task_config, state.db_cache, state.global_providers)
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
        
        for f in found_files:
            plugin.process_file(f, args.src)
            
        return {"status": "success", "files_scanned": len(found_files)}
        
    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/file/update")
async def update_metadata(update: MetadataUpdate):
    # Find which task owns this file
    target_task = None
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
        raise HTTPException(status_code=404, detail="Source root configuration not found for this file")

    db_path = task_config.get("db", "pal_database.yaml")
    db = state.db_cache.get(os.path.normpath(db_path))
    
    if not db:
        raise HTTPException(status_code=500, detail="Database not loaded")

    entry = db.get_video_entry(update.source_root, update.full_path)

    # Instantiate plugin
    result = prepare_task(task_config, state.db_cache, state.global_providers)
    if not result:
        raise HTTPException(status_code=500, detail="Plugin load failed")
    
    args, _, PluginClass = result
    plugin = PluginClass(db, args)
    
    old_target = entry.get("target_path") if entry else None
    old_hash = entry.get("metadata_hash")
    
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
    
    return {"status": "updated"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)