import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from openai import OpenAI
from guessit import guessit
from src.logger import get_logger

logger = get_logger(__name__)

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
        hash_data = {k: v for k, v in metadata.items() if k in ['title', 'season', 'episode', 'type']}
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

    def _extract_batch_llm(self, rel_dir, filenames, config):
        api_key = config.get("api_key")
        api_base = config.get("base_url")
        model = config.get("model")
        
        client = OpenAI(api_key=api_key, base_url=api_base)
        
        prompt = f"I have a TV series directory: '{rel_dir}' (relative path). "
        prompt += f"It contains these video files: {json.dumps(filenames)}. "
        prompt += "Please extract metadata for EACH file. "
        prompt += "Use the directory name to infer the Series Title and Season if possible. "
        prompt += "Return a JSON Object where keys are the filenames and values are metadata objects. "
        prompt += "Each metadata object must have: title (series name), season (int, default 1), episode (int), type='TV'. "
        prompt += "Do not include any markdown formatting, just the raw JSON."

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
            
            # Normalize int types
            for k, v in data.items():
                if k in filenames:
                    if v:
                        if isinstance(v.get("season"), str) and v["season"].isdigit(): v["season"] = int(v["season"])
                        if isinstance(v.get("episode"), str) and v["episode"].isdigit(): v["episode"] = int(v["episode"])
                        results[k] = v
                else:
                    logger.warning(f"LLM returned unexpected filename key: {k}")
            return results
        except Exception as e:
            logger.error(f"LLM Batch Error: {e}")
            return results
