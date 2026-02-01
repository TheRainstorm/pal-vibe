import os
import re
import json
import hashlib
from src.plugins.base import BaseVideoPlugin

class WebDLPlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "WebDL"

    def _get_guessit_options(self):
        return {} # Auto

    def _get_llm_prompt(self, filename):
        return (f"Extract metadata from WebDL filename '{filename}'. "
                "Return JSON with keys: title, type='WebDL'. "
                "Remove release groups/tags from title.")

    def _map_guessit_to_metadata(self, guess):
        title = guess.get("title")
        # Simple cleanup
        if title:
            title = re.sub(r'\[.*?\]', '', title)
            title = re.sub(r'(\s*-\s*)?(PROPER|REPACK|UNRATED|EXTENDED|DIRECTORS CUT|THEATRICAL|LIMITED|FESTIVAL|IMAX|WEB-DL|HDR|1080p|720p|2160p|4K)\b.*', '', title, flags=re.IGNORECASE).strip()
        
        return {
            "title": title,
            "year": str(guess.get("year")) if guess.get("year") else None,
            "type": "WebDL"
        }

    def calculate_hash(self, metadata):
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'year', 'type']}
        return hashlib.sha256(json.dumps(hash_data, sort_keys=True).encode('utf-8')).hexdigest()

    def generate_target_path(self, metadata, filepath):
        title = metadata.get("title")
        if not title: return None
        ext = os.path.splitext(filepath)[1]
        
        sub_folder = getattr(self.args, 'sub_folder', None) or "WebDL"
        return os.path.join(self.args.dst, sub_folder, title, f"{title}{ext}")