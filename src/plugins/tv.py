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
        if batch_size is None or batch_size <= 0: batch_size = 26
        
        final_batches = []
        for series_dir, files in groups.items():
            files.sort()
            for i in range(0, len(files), batch_size):
                chunk = files[i : i + batch_size]
                context = {'series_dir': series_dir, 'source_root': source_root}
                final_batches.append({'files': chunk, 'context': context})
        
        return final_batches

    # Override: Process batch with relative path context logic
    def _extract_batch_metadata(self, filenames, context):
        series_dir = context.get('series_dir')
        source_root = context.get('source_root')
        
        if series_dir == ".":
            base_dir = source_root
        else:
            base_dir = os.path.join(source_root, series_dir)

        # Map: Relative Path -> Absolute Path
        rel_to_abs = {}
        target_filenames = []
        
        for f in filenames:
            try:
                rel = os.path.relpath(f, base_dir)
                rel_to_abs[rel] = f
                target_filenames.append(rel)
            except ValueError:
                target_filenames.append(os.path.basename(f))

        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        raw_results = {} # { rel_path: metadata }

        for processor_name in chain:
            processor_name = processor_name.strip()
            current_results = {}
            
            if processor_name == 'guessit':
                logger.debug(f"Batch GuessIt for {len(target_filenames)} files in {series_dir}")
                current_results = self._extract_batch_guessit(base_dir, target_filenames)
            
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    logger.info(f"Batch LLM ({processor_name}) for {len(target_filenames)} files in {series_dir}")
                    current_results = self._extract_batch_llm(series_dir, target_filenames, config)
            
            elif processor_name == "cli_llm" and getattr(self.args, "llm_api_key", None):
                 config = {
                     "api_key": self.args.llm_api_key,
                     "base_url": self.args.llm_api_base,
                     "model": self.args.llm_model
                 }
                 logger.info(f"Batch CLI LLM for {len(target_filenames)} files in {series_dir}")
                 current_results = self._extract_batch_llm(series_dir, target_filenames, config)

            if current_results:
                succ, _ = self._validate_batch(current_results)
                if succ:
                    raw_results = current_results
                    break
        
        # Map back to Absolute Paths
        final_results = {}
        for rel, meta in raw_results.items():
            if rel in rel_to_abs:
                final_results[rel_to_abs[rel]] = meta
        
        return final_results

    def _extract_batch_llm(self, rel_dir, filenames, config):
        prompt = f"I have a TV series directory: '{rel_dir}' (relative path). "
        prompt += f"It contains these video files (paths relative to series dir): {json.dumps(filenames)}. "
        prompt += "Extract metadata for EACH file. "
        prompt += "Return JSON Object: { 'relative_path': { title, season(int), episode(int), type='TV' } }. "
        prompt += "Use directory structure to infer details."

        results = {fname:{} for fname in filenames}
        data = self._call_llm(config, prompt)
        
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

    def _extract_batch_guessit(self, base_dir, filenames):
        results = {}
        for rel_path in filenames:
            fake_path = os.path.join(base_dir, rel_path)
            options = self._get_guessit_options()
            guess = guessit(fake_path, options=options)
            results[rel_path] = self._map_guessit_to_metadata(guess)
        return results

    def _get_guessit_options(self): return {'type': 'episode'}
    
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
