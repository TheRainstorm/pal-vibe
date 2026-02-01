import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from src.metadata import generate_version_str

class MoviePlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "Movie"

    def _get_guessit_options(self):
        return {'type': 'movie'}

    def _get_llm_prompt(self, filename):
        return (f"Extract metadata from movie filename '{filename}'. "
                "Return JSON with keys: title, year (string), type='Movie'. "
                "Use null for missing fields.")

    def _map_guessit_to_metadata(self, guess):
        return {
            "title": guess.get("title"),
            "year": str(guess.get("year")) if guess.get("year") else None,
            "type": "Movie"
        }

    def calculate_hash(self, metadata):
        # Hash critical fields for Movies
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'year', 'type']}
        return hashlib.sha256(json.dumps(hash_data, sort_keys=True).encode('utf-8')).hexdigest()

    def generate_target_path(self, metadata, filepath):
        title = metadata.get("title")
        year = metadata.get("year")
        if not title: return None
        
        ext = os.path.splitext(filepath)[1]
        
        movie_dir_name = f"{title}"
        if year:
            movie_dir_name += f" ({year})"
        
        version_str = generate_version_str(metadata)
        filename_str = title
        if version_str:
            filename_str += f" - {version_str}"
        filename_str += ext

        sub_folder = getattr(self.args, 'sub_folder', None) or "Movie"
        return os.path.join(self.args.dst, sub_folder, movie_dir_name, filename_str)