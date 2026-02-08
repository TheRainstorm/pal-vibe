import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from guessit import guessit
from src.logger import get_logger

logger = get_logger(__name__)

class TVPlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "TV"

    # Override: Group by Series Directory
    def _group_files(self, filepaths):
        source_root = getattr(self.args, "src")
        groups = {} 
        
        for f in filepaths:
            try:
                rel = os.path.relpath(f, source_root)
                parts = rel.split(os.sep)
                if len(parts) > 1:
                    series_dir = parts[0]
                else:
                    series_dir = "." 
                
                if series_dir not in groups:
                    groups[series_dir] = []
                groups[series_dir].append(f)
            except ValueError:
                pass

        batch_size = getattr(self.args, 'batch_size', 26)
        
        final_batches = []
        for series_dir, files in groups.items():
            files.sort()
            for i in range(0, len(files), batch_size):
                chunk = files[i : i + batch_size]
                context = {'rel_dir': os.path.join(source_root, series_dir)}
                final_batches.append({'files': chunk, 'context': context})
        
        return final_batches

    def _get_guessit_options(self): return {'type': 'episode'}
    
    def _map_guessit_to_metadata(self, guess):
        return {
            "title": guess.get("title"),
            "season": guess.get("season", 1),
            "episode": guess.get("episode"),
            "type": "TV"
        }
    
    def _get_batch_llm_prompt(self, context, filenames):
        rel_dir = context.get('rel_dir')
        prompt = f"I have a TV series directory: '{rel_dir}' (relative path). "
        prompt += f"It contains these video files: {json.dumps(filenames)}. "
        prompt += "Please extract metadata for EACH file. "
        prompt += "Use the directory name to infer the Series Title and Season if possible. "
        prompt += "Return a JSON Object where keys are the filenames and values are metadata objects. "
        prompt += "Each metadata object must have: title (series name), season (int, default 1), episode (int)"
        prompt += "Do not include any markdown formatting, just the raw JSON."
        return prompt

    def _check_and_fix_metadata(self, meta):
        """
        convert integers, etc.
        """
        have_fixed = False
        def check_int_field(field_name):
            nonlocal have_fixed
            if field_name in meta:
                if isinstance(meta[field_name], str) and meta[field_name].isdigit():
                    meta[field_name] = int(meta[field_name])
                    have_fixed = True
        check_int_field('season')
        check_int_field('episode')
        return have_fixed

    def calculate_hash(self, metadata):
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'season', 'episode', 'type', 'ignore']}
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
        if not metadata:
            return False, "Metadata extraction failed"
        if not metadata.get("title"):
            return False, "Missing title"
        if metadata.get("episode") is None or type(metadata.get("episode")) is not int:
            return False, "Missing episode number"
        return True, "OK"
