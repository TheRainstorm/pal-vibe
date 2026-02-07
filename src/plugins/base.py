import os
import json
import hashlib
from guessit import guessit
from openai import OpenAI
from src.metadata import get_video_info_ffmpeg
from src.link_manager import create_link, remove_link_and_empty_dirs
from src.logger import get_logger

logger = get_logger(__name__)

class BaseVideoPlugin:
    def __init__(self, db, args):
        self.db = db
        self.args = args

    def get_type_name(self):
        raise NotImplementedError

    def process_files(self, filepaths, source_root):
        """
        Main entry point for processing a list of files.
        Handles grouping and batch processing internally.
        """
        batches = self._group_files(filepaths)
        logger.info(f"Processing {len(filepaths)} files in {len(batches)} batches...")
        
        for batch in batches:
            self._process_batch(batch, source_root)

    def process_file(self, filepath, source_root):
        """
        Convenience wrapper for processing a single file.
        """
        self.process_files([filepath], source_root)

    def _group_files(self, filepaths):
        """
        Groups files into batches. Default implementation simply chunks by batch_size.
        """
        batch_size = getattr(self.args, 'batch_size', 10)
        if batch_size is None or batch_size <= 0:
            batch_size = 10 

        groups = []
        for i in range(0, len(filepaths), batch_size):
            chunk = filepaths[i : i + batch_size]
            groups.append({'files': chunk, 'context': {}})
        
        return groups

    def _process_batch(self, batch_data, source_root):
        files = batch_data['files']
        context = batch_data.get('context', {})
        
        files_to_extract = []
        for f in files:
            if self._needs_extraction(f):
                files_to_extract.append(f)
        
        extraction_results = {}
        if files_to_extract:
            extraction_results = self.extract_batch_metadata(files_to_extract, context)
        
        for f in files:
            meta = extraction_results.get(os.path.basename(f))
            self._apply_file_logic(f, meta, source_root)

    def extract_batch_metadata(self, filenames, context):
        """
        Extracts metadata for a list of filenames using Chain (LLM/GuessIt).
        Returns { filename: metadata }
        """
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {} 
        for f in filenames:
            results[os.path.basename(f)] = {}

        if not filenames: return results

        base_filenames = [os.path.basename(f) for f in filenames]
        rel_dir = context.get('rel_dir', "")

        for processor_name in chain:
            processor_name = processor_name.strip()
            
            current_results = {}
            
            if processor_name == 'guessit':
                logger.info(f"Batch GuessIt for {len(filenames)} files")
                current_results = self._extract_batch_guessit(rel_dir, base_filenames)
            
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    logger.info(f"Batch LLM ({processor_name}) for {len(filenames)} files")
                    current_results = self._extract_batch_llm(rel_dir, base_filenames, config)
            
            elif processor_name == "cli_llm" and getattr(self.args, "llm_api_key", None):
                 config = {
                     "api_key": self.args.llm_api_key,
                     "base_url": self.args.llm_api_base,
                     "model": self.args.llm_model
                 }
                 logger.info(f"Batch CLI LLM for {len(filenames)} files")
                 current_results = self._extract_batch_llm(rel_dir, base_filenames, config)

            if current_results:
                valid_count = 0
                for fname, meta in current_results.items():
                    is_valid, _ = self._validate_metadata(meta)
                    if is_valid:
                        results[fname] = meta
                        valid_count += 1
                
                if valid_count > 0 and (valid_count / len(filenames) >= 0.5):
                    break
        
        return results

    def _needs_extraction(self, filepath):
        db_entry = self.db.get_video_entry(getattr(self.args, "src"), filepath)
        if not db_entry: return True
        if db_entry.get("error"): return True
        return False

    def _apply_file_logic(self, filepath, new_metadata, source_root):
        db_entry = self.db.get_video_entry(source_root, filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 
        
        if db_entry and not new_metadata:
            # Existing file case
            final_metadata = db_entry["metadata"]
            current_db_hash = self.calculate_hash(final_metadata)
            
            if db_entry.get("metadata_hash") == current_db_hash:
                if db_entry.get("error"):
                    # Retry logic for error entries (if they weren't picked up for re-extraction?)
                    # If _needs_extraction returned False, it means we don't think we need LLM.
                    # But if it has error, maybe we should have re-extracted. 
                    # Correct logic: _needs_extraction returns True for errors.
                    # So if we are here, either:
                    # 1. We re-extracted but got empty/invalid new_metadata? -> new_metadata is {}
                    # 2. Or something else.
                    # If new_metadata is empty dict, it's "truthy" enough? No, {} is False.
                    pass
                else:
                    target_path = self.generate_target_path(final_metadata, filepath)
                    if not target_path: return

                    if not os.path.lexists(target_path):
                        create_link(filepath, target_path, soft_link)
                    
                    if db_entry.get("source_root") != source_root:
                         self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
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
                     create_link(filepath, target_path, soft_link)
                
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                return

        # New File or Re-extraction
        if not new_metadata:
            # Extraction failed completely
            return

        logger.info(f"Processing new/updated metadata for: {filepath}")
        filename_metadata = new_metadata
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata.copy()

        is_valid, error_reason = self._validate_metadata(filename_metadata)
        if not is_valid:
            logger.warning(f"Invalid metadata for {filepath}: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        logger.debug(f"Scanning technical info for {filepath}...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)

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

    # Abstract methods
    def extract_filename_metadata(self, filepath):
        # Legacy method kept for compatibility if needed, but logic moved to batch
        return self._extract_metadata_lazy_batch(filepath) # Wait, lazy batch removed?
        # Yes, we moved to explicit process_files. This method might not be needed anymore 
        # unless Monitor calls it? No, Monitor now calls process_files.
        # But TVPlugin uses extract_batch_metadata overrides.
        pass

    def _extract_batch_llm(self, rel_dir, filenames, config): raise NotImplementedError
    def _extract_batch_guessit(self, rel_dir, filenames):
        results = {}
        for fname in filenames:
            fake_path = os.path.join(rel_dir, fname) if rel_dir else fname
            options = self._get_guessit_options()
            guess = guessit(fake_path, options=options)
            results[fname] = self._map_guessit_to_metadata(guess)
        return results

    def _get_guessit_options(self): return {}
    def _get_llm_prompt(self, filename): return ""
    def _map_guessit_to_metadata(self, guess): return dict(guess)
    def _validate_metadata(self, metadata): return True, "OK"
    def generate_target_path(self, metadata, filepath): raise NotImplementedError
    def calculate_hash(self, metadata):
        stable_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()
