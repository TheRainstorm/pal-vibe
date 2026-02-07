import os
import re
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from src.logger import get_logger

logger = get_logger(__name__)

class WebDLPlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "WebDL"

    def _get_guessit_options(self):
        return {} 

    def _map_guessit_to_metadata(self, guess):
        title = guess.get("title")
        if title:
            title = re.sub(r'\[.*?\]', '', title)
            title = re.sub(r'(\s*-\s*)?(PROPER|REPACK|UNRATED|EXTENDED|DIRECTORS CUT|THEATRICAL|LIMITED|FESTIVAL|IMAX|WEB-DL|HDR|1080p|720p|2160p|4K)\b.*', '', title, flags=re.IGNORECASE).strip()
        
        return {
            "title": title,
            "year": str(guess.get("year")) if guess.get("year") else None,
            "type": "WebDL"
        }

    def _get_batch_llm_prompt(self, filenames):
        prompt = f"Extract metadata for these WebDL files: {json.dumps(filenames)}. "
        prompt += "Return a JSON Object where keys are filenames and values are metadata objects. "
        prompt += "Each metadata object must have: title (string), year (string), type='WebDL'. "
        prompt += "Clean title by removing release groups/tags. "
        prompt += "Use null for missing fields."
        return prompt

    def calculate_hash(self, metadata):
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'year', 'type']}
        return hashlib.sha256(json.dumps(hash_data, sort_keys=True).encode('utf-8')).hexdigest()

    def generate_target_path(self, metadata, filepath):
        title = metadata.get("title")
        if not title: return None
        ext = os.path.splitext(filepath)[1]
        
        sub_folder = getattr(self.args, 'sub_folder', None) or "WebDL"
        return os.path.join(self.args.dst, sub_folder, title, f"{title}{ext}")