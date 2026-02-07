import os
import json
import hashlib
from src.plugins.base import BaseVideoPlugin
from openai import OpenAI
from guessit import guessit
from src.metadata import VIDEO_EXTENSIONS
from src.logger import get_logger

logger = get_logger(__name__)

class TVPlugin(BaseVideoPlugin):
    def get_type_name(self):
        return "TV"

    def __init__(self, db, args):
        super().__init__(db, args)
        self.batch_cache = {} # { dir_path: { filename: metadata } }

    def extract_filename_metadata(self, filepath, source_root=None):
        dirname = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        
        # Check cache
        if dirname in self.batch_cache:
            if filename in self.batch_cache[dirname]:
                logger.debug(f"Cached metadata hit")
                return self.batch_cache[dirname][filename]
        
        # Cache miss: Trigger batch processing for this directory
        # Find all sibling video files
        siblings = []
        try:
            # We only care about siblings that are video files to save tokens
            for f in os.listdir(dirname):
                if f.lower().endswith(VIDEO_EXTENSIONS):
                    siblings.append(f)
        except OSError:
            siblings = [filename]
        siblings.sort()

        # Calculate relative directory path for context (e.g. "SeriesName/Season 1")
        rel_dir = ""
        if source_root:
            try:
                rel_dir = os.path.relpath(dirname, source_root)
                if rel_dir == ".": rel_dir = ""
            except ValueError:
                pass # Different drive

        # Determine processor
        # We look at the chain. If any LLM provider is in the chain, we prioritize batch LLM.
        # This is a bit of a deviation from strict chain order, but beneficial for TV context.
        # Alternatively, strict chain:
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {}

        for processor_name in chain:
            processor_name = processor_name.strip()
            
            if processor_name == 'guessit':
                # Use batch guessit (enhanced with path)
                logger.info(f"Batch GuessIt for {len(siblings)} files in '{rel_dir}'")
                results = self._extract_batch_guessit(rel_dir, siblings)
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    logger.info(f"Batch LLM ({processor_name}) for {len(siblings)} files in '{rel_dir}'")
                    results = self._extract_batch_llm(rel_dir, siblings, config)
            elif processor_name == "cli_llm" and getattr(self.args, "llm_api_key", None):
                 config = {
                     "api_key": self.args.llm_api_key,
                     "base_url": self.args.llm_api_base,
                     "model": self.args.llm_model
                 }
                 logger.info(f"Batch CLI LLM for {len(siblings)} files in '{rel_dir}'")
                 results = self._extract_batch_llm(rel_dir, siblings, config)

        # Update cache
        if results:
            self.batch_cache[dirname] = results
            return results.get(filename, {})
        else:
            # If all failed, store empty to avoid re-scanning? 
            self.batch_cache[dirname] = results
            return {}

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
            if not isinstance(data, dict): return {}
            
            # Normalize int types
            for k, v in data.items():
                if v:
                    if isinstance(v.get("season"), str) and v["season"].isdigit(): v["season"] = int(v["season"])
                    if isinstance(v.get("episode"), str) and v["episode"].isdigit(): v["episode"] = int(v["episode"])
            
            return data
        except Exception as e:
            logger.error(f"LLM Batch Error: {e}")
            return {}

    def _extract_batch_guessit(self, rel_dir, filenames):
        results = {}
        for fname in filenames:
            # Construct a path that helps guessit: rel_dir + filename
            # e.g. "Breaking Bad/Season 1/01.mkv"
            fake_path = os.path.join(rel_dir, fname) if rel_dir else fname
            
            # Use guessit on the path
            guess = guessit(fake_path, options={'type': 'episode'})
            results[fname] = self._map_guessit_to_metadata(guess)
        return results

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
        # TV requires both title and episode
        if not metadata:
            return False, "Metadata extraction failed"
        if not metadata.get("title"):
            return False, "Missing title"
        if metadata.get("episode") is None or type(metadata.get("episode")) is not int:
            return False, "Missing episode number"
        return True, "OK"