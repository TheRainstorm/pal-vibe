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
                "Return JSON with keys: title, year (string), season (int), episode (int), ep_title, type='TV'. "
                "Default season to 1 if missing. Use null for missing fields.")

    def _map_guessit_to_metadata(self, guess):
        return {
            "title": guess.get("title"),
            "year": str(guess.get("year")) if guess.get("year") else None,
            "season": guess.get("season", 1),
            "episode": guess.get("episode"),
            "ep_title": guess.get("episode_title"),
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
        ep_title = metadata.get("ep_title")
        ext = os.path.splitext(filepath)[1]

        series_dir = f"{title}"
        if metadata.get("year"):
            series_dir += f" ({metadata['year']})"
        
        season_dir = f"Season {season:02d}"
        
        filename_str = f"S{season:02d}"
        if episode:
            filename_str += f"E{episode:02d}"
        if ep_title:
            filename_str += f" - {ep_title}"
        filename_str += ext

        return os.path.join(self.args.dst, self.args.tv_folder, series_dir, season_dir, filename_str)
