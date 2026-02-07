import os
import yaml
import json
from src.logger import get_logger

logger = get_logger(__name__)

class VideoDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.data = self._load_database()
        
        # Structure migration: Flat 'files' -> Nested 'roots'
        if "files" in self.data and self.data["files"]:
            logger.info("Migrating database to relative path structure...")
            if "roots" not in self.data:
                self.data["roots"] = {}
            
            for abs_path, entry in self.data["files"].items():
                src_root = entry.get("source_root")
                if not src_root: continue # Skip corrupted entries
                
                # Ensure root entry exists
                if src_root not in self.data["roots"]:
                    self.data["roots"][src_root] = {"files": {}, "dst_root": ""} # dst_root unknown during migration, logic handles empty
                
                try:
                    rel_path = os.path.relpath(abs_path, src_root)
                    
                    self.data["roots"][src_root]["files"][rel_path] = {
                        "metadata": entry.get("metadata"),
                        "metadata_hash": entry.get("metadata_hash"),
                        "target": entry.get("target_path"), # Potentially absolute, will be fixed on next run
                        "error": entry.get("error")
                    }
                except ValueError:
                    logger.warning(f"Skipping migration for {abs_path}: path not within source root {src_root}")
            
            del self.data["files"]
            self._save_database()

        if "roots" not in self.data:
            self.data["roots"] = {}

    def _load_database(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Failed to load database {self.db_path}: {e}")
                return {}
        return {}

    def _save_database(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self.data, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.error(f"Failed to save database {self.db_path}: {e}")

    def get_video_entry(self, src_root, src_filepath):
        src_root = os.path.normpath(src_root)
        src_filepath = os.path.normpath(src_filepath)
        
        root_data = self.data["roots"].get(src_root)
        if not root_data:
            return None
            
        try:
            rel_path = os.path.relpath(src_filepath, src_root)
        except ValueError:
            return None

        entry = root_data["files"].get(rel_path)
        if not entry:
            return None
            
        # Reconstruct absolute paths for the caller
        abs_target_path = None
        dst_root = root_data.get("dst_root")
        stored_target = entry.get("target")
        
        if stored_target:
            if os.path.isabs(stored_target):
                abs_target_path = stored_target # Fallback
            elif dst_root:
                abs_target_path = os.path.join(dst_root, stored_target)
        
        return {
            "metadata": entry.get("metadata"),
            "metadata_hash": entry.get("metadata_hash"),
            "target_path": abs_target_path,
            "error": entry.get("error"),
            "dst_root": dst_root
        }

    def update_video_entry(self, src_root, dst_root, src_filepath, metadata, target_path, metadata_hash, error=None):
        src_root = os.path.normpath(src_root)
        dst_root = os.path.normpath(dst_root)
        src_filepath = os.path.normpath(src_filepath)
        
        if src_root not in self.data["roots"]:
            self.data["roots"][src_root] = {"files": {}, "dst_root": dst_root}
        
        # Update dst_root if changed
        self.data["roots"][src_root]["dst_root"] = dst_root
        
        rel_src = os.path.relpath(src_filepath, src_root)
        rel_target = None
        if target_path:
            target_path = os.path.normpath(target_path)
            try:
                rel_target = os.path.relpath(target_path, dst_root)
            except ValueError:
                rel_target = target_path # Fallback to absolute if on different drive

        entry = {
            "metadata": metadata,
            "metadata_hash": metadata_hash,
            "target": rel_target
        }
        if error:
            entry["error"] = error
            
        self.data["roots"][src_root]["files"][rel_src] = entry
        self._save_database()

    def remove_entry(self, src_root, src_filepath):
        src_root = os.path.normpath(src_root)
        src_filepath = os.path.normpath(src_filepath)
        
        if src_root in self.data["roots"]:
            try:
                rel_src = os.path.relpath(src_filepath, src_root)
                if rel_src in self.data["roots"][src_root]["files"]:
                    del self.data["roots"][src_root]["files"][rel_src]
                    self._save_database()
                    return True
            except ValueError:
                pass
        return False

    def get_files_by_source_root(self, src_root):
        """
        Returns a list of ABSOLUTE filepaths that belong to the given source_root.
        """
        src_root = os.path.normpath(src_root)
        root_data = self.data["roots"].get(src_root)
        if not root_data:
            return []
            
        return [
            os.path.join(src_root, rel_path) 
            for rel_path in root_data["files"].keys()
        ]
    
    def get_file_by_target_path(self, target_path):
        """
        Finds if any file is already linked to the given target_path (Absolute).
        Returns the source filepath (Absolute) if found, None otherwise.
        Checking ALL roots for collision.
        """
        target_path = os.path.normpath(target_path)
        
        for src_root, root_data in self.data["roots"].items():
            dst_root = root_data.get("dst_root")
            if not dst_root: continue
            
            try:
                if not target_path.startswith(dst_root):
                    continue
            except:
                continue

            for rel_src, entry in root_data["files"].items():
                rel_target = entry.get("target")
                if not rel_target: continue
                
                if os.path.isabs(rel_target):
                    abs_db_target = rel_target
                else:
                    abs_db_target = os.path.join(dst_root, rel_target)
                
                if abs_db_target == target_path:
                    return os.path.join(src_root, rel_src)
        return None
