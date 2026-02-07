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

    def group_files(self, filepaths):
        """
        Groups files into batches. Default implementation simply chunks by batch_size.
        """
        batch_size = getattr(self.args, 'batch_size', 10)
        if batch_size is None or batch_size <= 0:
            batch_size = 10 # Default to 10 if invalid

        groups = []
        # Simple chunking
        for i in range(0, len(filepaths), batch_size):
            chunk = filepaths[i : i + batch_size]
            groups.append({'files': chunk, 'context': {}})
        
        return groups

    def process_batch(self, batch_data, source_root):
        files = batch_data['files']
        context = batch_data.get('context', {})
        
        # 1. Determine which files actually need metadata extraction
        # (i.e. not in DB or hash mismatch)
        files_to_extract = []
        for f in files:
            if self._needs_extraction(f):
                files_to_extract.append(f)
        
        extraction_results = {}
        if files_to_extract:
            # 2. Extract metadata for the subset that needs it
            extraction_results = self.extract_batch_metadata(files_to_extract, context)
        
        # 3. Apply logic to all files (link creation, db update)
        for f in files:
            # If we extracted new metadata, use it. 
            # Otherwise, _apply_file_logic will fetch from DB.
            meta = extraction_results.get(os.path.basename(f))
            self._apply_file_logic(f, meta, source_root)

    def extract_batch_metadata(self, filenames, context):
        """
        Extracts metadata for a list of filenames using Chain (LLM/GuessIt).
        Returns { filename: metadata }
        """
        # Logic similar to previous extract_filename_metadata but for a list
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {} # { filename: metadata }
        # Initialize with empty
        for f in filenames:
            results[os.path.basename(f)] = {}

        # If list is empty
        if not filenames: return results

        base_filenames = [os.path.basename(f) for f in filenames]
        # Context might contain rel_dir for TV
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

            # Validate Batch
            if current_results:
                # Merge valid results
                valid_count = 0
                for fname, meta in current_results.items():
                    is_valid, _ = self._validate_metadata(meta)
                    if is_valid:
                        results[fname] = meta
                        valid_count += 1
                
                # Check ratio (simplified validation)
                if valid_count > 0 and (valid_count / len(filenames) >= 0.5):
                    break
        
        return results

    def _needs_extraction(self, filepath):
        db_entry = self.db.get_video_entry(getattr(self.args, "src"), filepath) # source_root is stored in args.src usually, but careful
        # Wait, args.src might not be source_root if we run multiple tasks.
        # But BaseVideoPlugin is instantiated per task. So self.args IS the task config.
        # However, db.get_video_entry needs the correct source_root.
        # Let's assume self.args.src is correct for this plugin instance.
        
        if not db_entry: return True
        
        # If in DB, check hash?
        # But we can't check hash without extracting metadata first!
        # This is the Catch-22.
        # If we want to skip extraction, we must trust the DB entry unless we have reason to doubt.
        # The previous logic was: Extract -> Calculate Hash -> Compare with DB.
        # To optimize, we ONLY want to extract if we think it changed?
        # No, we MUST extract to know if it changed (filename based).
        # UNLESS we assume filename changes mean new files.
        # If filename is same, metadata from filename is same.
        # So: If filepath exists in DB, we DO NOT need to extract from LLM/GuessIt again!
        # Because the input (filename) hasn't changed.
        # EXCEPT if the user changed the logic/prompt? We assume stable logic.
        
        # Correction: If the user manually edited the DB, the hash in DB will NOT match the calculated hash of the *filename metadata*.
        # So we DO need to extract filename metadata to compare.
        # BUT, calculating hash requires extracting metadata.
        # So we cannot skip extraction if we want to detect manual DB edits vs File Metadata mismatch.
        
        # Wait, the logic in previous process_file was:
        # 1. Get DB entry.
        # 2. Calc hash of DB entry's metadata.
        # 3. Compare with DB entry's stored hash.
        # If match: User didn't edit DB. And since filename didn't change, we assume metadata is valid. SKIP.
        # If mismatch: User edited DB. We USE the DB data. SKIP extraction.
        
        # So actually, we NEVER need to re-extract filename metadata for existing files!
        # Because:
        # A) User didn't touch DB -> DB is valid cache of filename metadata.
        # B) User touched DB -> We trust user's edit over filename metadata.
        
        return False

    def _apply_file_logic(self, filepath, new_metadata, source_root):
        db_entry = self.db.get_video_entry(source_root, filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 
        
        # If we have new_metadata (from batch extraction), it means it's a new file 
        # (or we forced extraction).
        
        if db_entry and not new_metadata:
            # Existing file case (Skipped extraction)
            final_metadata = db_entry["metadata"]
            current_db_hash = self.calculate_hash(final_metadata)
            
            if db_entry.get("metadata_hash") == current_db_hash:
                # Case A: No manual changes
                if db_entry.get("error"):
                    # Retry logic? If it failed before, maybe we SHOULD have re-extracted?
                    # If it was an error, _needs_extraction should have returned True?
                    # Let's fix _needs_extraction to return True for errors.
                    pass 
                else:
                    # Valid, unchanged. Ensure link.
                    target_path = self.generate_target_path(final_metadata, filepath)
                    if not target_path: return

                    if not os.path.lexists(target_path):
                        create_link(filepath, target_path, soft_link)
                    
                    if db_entry.get("source_root") != source_root:
                         self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                    return

            else:
                # Case B: User manually edited DB
                logger.info(f"Metadata changed (corrected by human), re-processing: {filepath}")
                
                # Validate user data
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

        # Case C: New File (or Error Retry)
        if not new_metadata:
            # Should not happen if _needs_extraction worked right
            logger.error(f"No metadata extracted for new file: {filepath}")
            return

        logger.info(f"New file processed: {filepath}")
        filename_metadata = new_metadata
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata.copy()

        # Check validation
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        if not is_valid:
            logger.warning(f"Invalid metadata for {filepath}: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        # FFmpeg scan
        logger.debug(f"Scanning technical info for {filepath}...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)

        # Generate Link
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

    # Fix _needs_extraction to handle errors correctly
    def _needs_extraction(self, filepath):
        db_entry = self.db.get_video_entry(getattr(self.args, "src"), filepath)
        if not db_entry: return True
        if db_entry.get("error"): return True # Retry errors
        # If db_entry exists and no error, we assume we don't need to re-extract from filename
        # because filename hasn't changed.
        return False

    # Keep abstract methods...
    def _extract_batch_llm(self, rel_dir, filenames, config):
        raise NotImplementedError

    def _extract_batch_guessit(self, rel_dir, filenames):
        # Default implementation from previous TVPlugin logic, generalized
        results = {}
        for fname in filenames:
            fake_path = os.path.join(rel_dir, fname) if rel_dir else fname
            options = self._get_guessit_options()
            guess = guessit(fake_path, options=options)
            results[fname] = self._map_guessit_to_metadata(guess)
        return results

    # Legacy methods needed for single file fallback or subclass use
    def _get_guessit_options(self): return {}
    def _get_llm_prompt(self, filename): return ""
    def _map_guessit_to_metadata(self, guess): return dict(guess)
    def _validate_metadata(self, metadata): return True, "OK"
    def generate_target_path(self, metadata, filepath): raise NotImplementedError
    def calculate_hash(self, metadata):
        stable_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()
    
    # process_file wrapper for Monitor/Single call compatibility
    def process_file(self, filepath, source_root):
        # Just wrap as a batch of 1
        self.process_batch({'files': [filepath], 'context': {}}, source_root)