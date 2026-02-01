import os
import yaml
import json

class VideoDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.data = self._load_database()
        if "files" not in self.data:
            self.data["files"] = {}
        
        # Clean up legacy error_files if it exists
        if "error_files" in self.data:
            del self.data["error_files"]

    def _load_database(self):
        if os.path.exists(self.db_path):
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_database(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, default_flow_style=False)

    def get_video_entry(self, src_filepath):
        return self.data["files"].get(src_filepath)

    def update_video_entry(self, src_filepath, metadata, target_path, metadata_hash, source_root, error=None):
        entry = {
            "source_root": source_root,
            "metadata": metadata,
            "target_path": target_path,
            "metadata_hash": metadata_hash
        }
        if error:
            entry["error"] = error
        
        self.data["files"][src_filepath] = entry
        self._save_database()

    def remove_entry(self, src_filepath):
        if src_filepath in self.data["files"]:
            del self.data["files"][src_filepath]
            self._save_database()
            return True
        return False

    def get_all_files(self):
        return list(self.data["files"].keys())

    def get_files_by_source_root(self, source_root):
        """
        Returns a list of filepaths that belong to the given source_root.
        """
        return [
            fpath for fpath, data in self.data["files"].items() 
            if data.get("source_root") == source_root
        ]
    
    def get_file_by_target_path(self, target_path):
        """
        Finds if any file is already linked to the given target_path.
        Returns the source filepath if found, None otherwise.
        """
        for fpath, data in self.data["files"].items():
            if data.get("target_path") == target_path:
                return fpath
        return None