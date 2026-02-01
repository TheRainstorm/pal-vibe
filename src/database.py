import os
import yaml
import json

class VideoDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.data = self._load_database()
        if "files" not in self.data:
            self.data["files"] = {}
        if "error_files" not in self.data:
            self.data["error_files"] = []

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

    def update_video_entry(self, src_filepath, metadata, target_path, metadata_hash):
        self.data["files"][src_filepath] = {
            "metadata": metadata,
            "target_path": target_path,
            "metadata_hash": metadata_hash
        }
        # If it was in error list, remove it
        if src_filepath in self.data["error_files"]:
            self.data["error_files"].remove(src_filepath)
        self._save_database()

    def remove_entry(self, src_filepath):
        if src_filepath in self.data["files"]:
            del self.data["files"][src_filepath]
            self._save_database()
            return True
        return False

    def add_error_file(self, src_filepath):
        if src_filepath not in self.data["error_files"]:
            self.data["error_files"].append(src_filepath)
            self._save_database()

    def is_error_file(self, src_filepath):
        return src_filepath in self.data["error_files"]

    def get_all_files(self):
        return list(self.data["files"].keys())
