import os
import json
import hashlib
from guessit import guessit
from openai import OpenAI
from src.metadata import get_video_info_ffmpeg, VIDEO_EXTENSIONS
from src.link_manager import create_link, remove_link_and_empty_dirs
from src.logger import get_logger

logger = get_logger(__name__)

class BaseVideoPlugin:
    def __init__(self, db, args):
        self.db = db
        self.args = args
        self.batch_cache = {} # { dir_path: { filename: metadata } }

    def get_type_name(self):
        raise NotImplementedError

    def extract_filename_metadata(self, filepath, source_root=None):
        """
        Uses lazy batch processing to extract metadata.
        """
        return self._extract_metadata_lazy_batch(filepath, source_root)

    def _extract_metadata_lazy_batch(self, filepath, source_root):
        dirname = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        
        # Check cache
        if dirname in self.batch_cache:
            if filename in self.batch_cache[dirname]:
                logger.debug(f"Cached metadata hit for {filename}")
                return self.batch_cache[dirname][filename]
            else:
                # File not in the cached batch (maybe added later or batching logic skipped it)
                # We can try to process it individually or trigger a re-batch?
                # For simplicity, let's treat it as a miss and re-trigger batch logic for its group
                pass 
        
        # Cache miss: Trigger batch processing
        # 1. Identify all candidates in directory
        siblings = []
        try:
            for f in os.listdir(dirname):
                if f.lower().endswith(VIDEO_EXTENSIONS):
                    siblings.append(f)
        except OSError:
            siblings = [filename]
        siblings.sort()

        # 2. Determine batch size
        # Default 10 if not set. -1 means all.
        batch_size = getattr(self.args, 'batch_size', 10)
        if batch_size is None: batch_size = 10
        if batch_size <= 0:
            batch_size = len(siblings)

        # 3. Find which batch current file belongs to
        target_batch = []
        for i in range(0, len(siblings), batch_size):
            batch = siblings[i : i + batch_size]
            if filename in batch:
                target_batch = batch
                break
        
        if not target_batch:
            target_batch = [filename] # Should not happen

        # 4. Context info
        rel_dir = ""
        if source_root:
            try:
                rel_dir = os.path.relpath(dirname, source_root)
                if rel_dir == ".": rel_dir = ""
            except ValueError:
                pass

        # 5. Execute processing chain
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {} # { filename: metadata }
        
        for processor_name in chain:
            processor_name = processor_name.strip()
            
            if processor_name == 'guessit':
                logger.info(f"Batch GuessIt for {len(target_batch)} files in '{rel_dir}'")
                results = self._extract_batch_guessit(rel_dir, target_batch)
            
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    logger.info(f"Batch LLM ({processor_name}) for {len(target_batch)} files in '{rel_dir}'")
                    results = self._extract_batch_llm(rel_dir, target_batch, config)
            
            elif processor_name == "cli_llm" and getattr(self.args, "llm_api_key", None):
                 config = {
                     "api_key": self.args.llm_api_key,
                     "base_url": self.args.llm_api_base,
                     "model": self.args.llm_model
                 }
                 logger.info(f"Batch CLI LLM for {len(target_batch)} files in '{rel_dir}'")
                 results = self._extract_batch_llm(rel_dir, target_batch, config)

            # Validate batch
            succ, ratio = self._validate_batch(results)
            logger.debug(f"{processor_name} valid ratio: {ratio:.0%}")
            if succ:
                break
        
        # 6. Update cache
        if dirname not in self.batch_cache:
            self.batch_cache[dirname] = {}
        self.batch_cache[dirname].update(results)
        
        return results.get(filename, {})

    def _validate_batch(self, results):
        if not results: return False, 0
        valid_count = 0
        total_count = len(results)
        for meta in results.values():
            is_valid, _ = self._validate_metadata(meta)
            if is_valid:
                valid_count += 1
        ratio = valid_count / total_count if total_count > 0 else 0
        return ratio >= 0.5, ratio

    # Abstract methods for subclasses to implement specifics
    def _extract_batch_llm(self, rel_dir, filenames, config):
        # Default implementation: Iterate one by one (fallback) or use generic prompt?
        # Better to force subclasses to implement optimized prompts.
        raise NotImplementedError("Subclasses must implement _extract_batch_llm")

    def _extract_batch_guessit(self, rel_dir, filenames):
        results = {}
        for fname in filenames:
            fake_path = os.path.join(rel_dir, fname) if rel_dir else fname
            options = self._get_guessit_options()
            guess = guessit(fake_path, options=options)
            results[fname] = self._map_guessit_to_metadata(guess)
        return results

    # Legacy single file methods kept for reference or specific overrides
    def _get_guessit_options(self):
        return {}

    def _map_guessit_to_metadata(self, guess):
        return dict(guess)

    def _validate_metadata(self, metadata):
        if not metadata or not metadata.get("title"):
            return False, "Missing title"
        return True, "OK"

    def generate_target_path(self, metadata, filepath):
        raise NotImplementedError

    def calculate_hash(self, metadata):
        stable_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()

    def process_file(self, filepath, source_root):
        db_entry = self.db.get_video_entry(source_root, filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 
        
        # Step 1: Check against DB
        if db_entry:
            final_metadata = db_entry["metadata"]
            current_db_hash = self.calculate_hash(final_metadata)
            
            if db_entry.get("metadata_hash") == current_db_hash:
                if db_entry.get("error"):
                    is_valid, error_reason = self._validate_metadata(final_metadata)
                    if not is_valid: return 
                    logger.info(f"File previously had error but now seems valid. Retrying: {filepath}")
                else:
                    target_path = self.generate_target_path(final_metadata, filepath)
                    if not target_path: return 

                    if not os.path.lexists(target_path):
                        logger.info(f"Link missing, recreating: {target_path}")
                        create_link(filepath, target_path, soft_link)
                    
                    if db_entry.get("source_root") != source_root:
                         logger.debug(f"Updating source_root for {filepath}")
                         self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                    
                    logger.debug(f"No change detected for: {filepath}")
                    return 

            else:
                logger.info(f"Metadata changed (corrected by human), re-processing: {filepath}")
                is_valid, error_reason = self._validate_metadata(final_metadata)
                
                if not is_valid:
                    logger.warning(f"User edit is still invalid: {error_reason}")
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=error_reason)
                    return

                target_path = self.generate_target_path(final_metadata, filepath)
                if not target_path:
                    logger.error(f"Cannot generate target path for {filepath}")
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error="Cannot generate target path")
                    return

                existing_owner = self.db.get_file_by_target_path(target_path)
                if existing_owner and existing_owner != filepath:
                    logger.error(f"Target path conflict: {target_path} used by {existing_owner}")
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=f"Target path conflict with {existing_owner}")
                    return

                old_target_path = db_entry.get("target_path")
                if target_path and target_path != old_target_path:
                     logger.info(f"Target path changed. Relinking to: {target_path}")
                     if old_target_path:
                        remove_link_and_empty_dirs(old_target_path)
                     create_link(filepath, target_path, soft_link)
                elif not os.path.lexists(target_path):
                     logger.info(f"Link missing, creating: {target_path}")
                     create_link(filepath, target_path, soft_link)
                
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                return 

        # Step 2: New File
        logger.info(f"New file found: {filepath}")
        filename_metadata = self.extract_filename_metadata(filepath, source_root)
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata.copy()
        logger.debug(f"Extract filename info: {filename_metadata}")

        # Step 3: Check validation
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        
        if not is_valid:
            logger.warning(f"Invalid metadata for {filepath}: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        # Step 4: FFmpeg scan
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        logger.debug(f"Scanning ffmpeg info: {ffmpeg_info}")
        final_metadata.update(ffmpeg_info)

        # Step 5: Generate Link
        target_path = self.generate_target_path(final_metadata, filepath)
        
        if target_path:
            existing_owner = self.db.get_file_by_target_path(target_path)
            if existing_owner and existing_owner != filepath:
                logger.error(f"Target path conflict: {target_path} used by {existing_owner}")
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=f"Target path conflict with {existing_owner}")
                return

            create_link(filepath, target_path, soft_link)
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_hash, error=None)
        else:
            logger.error(f"Could not generate target path for {filepath}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error="Could not generate target path")
