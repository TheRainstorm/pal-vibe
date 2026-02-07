import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from src.metadata import generate_version_str
from openai import OpenAI
from src.logger import get_logger

logger = get_logger(__name__)

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

    def _extract_batch_llm(self, rel_dir, filenames, config):
        api_key = config.get("api_key")
        api_base = config.get("base_url")
        model = config.get("model")
        
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        prompt = f"Extract metadata for these MOVIE files: {json.dumps(filenames)}. "
        prompt += "Return a JSON Object where keys are filenames and values are metadata objects. "
        prompt += "Each metadata object must have: title (string), year (string), type='Movie'. "
        prompt += "Use null for missing fields."

        results = {fname:{} for fname in filenames}
        try:
            chat_completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "text"} 
            )
            content = chat_completion.choices[0].message.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
            
            data = json.loads(content)
            if not isinstance(data, dict): return results
            
            for k, v in data.items():
                if k in filenames and v:
                    if v.get("year"): v["year"] = str(v["year"])
                    v["type"] = "Movie"
                    results[k] = v
            return results
        except Exception as e:
            logger.error(f"LLM Batch Error: {e}")
            return results

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
