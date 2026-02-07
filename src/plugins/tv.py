import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin

class TVPlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "TV"

    def _get_guessit_options(self):
        return {'type': 'episode'}

    def _get_llm_prompt(self, filename):
        return (f"Extract metadata from TV episode filename '{filename}'. "
                "Return JSON with keys: title, season (int), episode (int), type='TV'. "
                "Default season to 1 if missing. Use null for missing fields.")

    def _map_guessit_to_metadata(self, guess):
        return {
            "title": guess.get("title"),
            "season": guess.get("season", 1),
            "episode": guess.get("episode"),
            "type": "TV"
        }
    
    def calculate_hash(self, metadata):
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'year', 'season', 'episode', 'ep_title', 'type']}
        return hashlib.sha256(json.dumps(hash_data, sort_keys=True).encode('utf-8')).hexdigest()

    def generate_target_path(self, metadata, filepath):
        title = metadata.get("title")
        if not title: return None
        
        season = metadata.get("season", 1)
        episode = metadata.get("episode")
        ext = os.path.splitext(filepath)[1]

        series_dir = f"{title}"
        season_dir = f"Season {season:02d}"
        
        filename_str = f"{title}-S{season:02d}E{episode:02d}{ext}"

        sub_folder = getattr(self.args, 'sub_folder', None) or "TV"
        return os.path.join(self.args.dst, sub_folder, series_dir, season_dir, filename_str)

    def _validate_metadata(self, metadata):
        # TV requires both title and episode
        if not metadata:
            return False, "Metadata extraction failed"
        if not metadata.get("title"):
            return False, "Missing title"
        if metadata.get("episode") is None:
            return False, "Missing episode number"
        return True, "OK"
