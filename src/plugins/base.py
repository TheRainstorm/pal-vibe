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
    """
    Base Plugin implementing the core logic for metadata extraction and linking.

    Processing Flow:
    ----------------
    process_files(filepaths)
       |
       v
    _group_files(filepaths) -> batches
       |
       v
    Loop over batches:
       _process_batch(batch)
          |
          +--> _needs_extraction(file)?
          |      |
          |      +-> Yes -> _extract_batch_metadata(files) -> results
          |      +-> No  -> (Skip extraction)
          |
          v
       Loop over files in batch:
          _apply_file_logic(file, new_metadata)
             |
             +--> DB Entry exists?
             |      |
             |      +-> Yes -> Check Hash / Retry Flag
             |      |            |
             |      |            +-> Match & No Retry -> Check Link -> Done
             |      |            +-> Mismatch / Retry -> Update Meta -> Re-Link -> Done
             |      |
             |      +-> No -> Use new_metadata -> Validate -> Scan FFmpeg -> Link -> Save DB
    """

    def __init__(self, db, args):
        self.db = db
        self.args = args

    # =========================================================================
    # Public API
    # =========================================================================

    def get_type_name(self):
        raise NotImplementedError

    def process_files(self, filepaths, source_root):
        """
        Main entry point. Groups files and processes them in batches.
        """
        batches = self._group_files(filepaths)
        logger.info(f"Processing {len(filepaths)} files in {len(batches)} batches...")
        
        for batch in batches:
            self._process_batch(batch, source_root)

    def process_file(self, filepath, source_root):
        """Wrapper for single file processing."""
        self.process_files([filepath], source_root)

    # =========================================================================
    # Template Methods (Subclasses should override these)
    # =========================================================================

    def _group_files(self, filepaths):
        """
        Default: Chunk by batch_size.
        """
        batch_size = getattr(self.args, 'batch_size', 26)
        if batch_size is None or batch_size <= 0: batch_size = 26

        groups = []
        for i in range(0, len(filepaths), batch_size):
            chunk = filepaths[i : i + batch_size]
            groups.append({'files': chunk, 'context': {}})
        return groups

    def _extract_batch_llm(self, rel_dir, filenames, config):
        raise NotImplementedError

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
    
    def _validate_metadata(self, metadata): 
        if not metadata or not metadata.get("title"):
            return False, "Missing title"
        return True, "OK"

    def generate_target_path(self, metadata, filepath):
        raise NotImplementedError

    def calculate_hash(self, metadata):
        stable_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()

    # =========================================================================
    # Internal Logic
    # =========================================================================

    def _process_batch(self, batch_data, source_root):
        files = batch_data['files']
        context = batch_data.get('context', {})
        
        files_to_extract = []
        for f in files:
            if self._needs_extraction(f):
                files_to_extract.append(f)
        
        extraction_results = {}
        if files_to_extract:
            extraction_results = self._extract_batch_metadata(files_to_extract, context)
        
        for f in files:
            # Retrieve metadata from batch results
            # Default: key is basename. Subclasses (TVPlugin) might return absolute path keys.
            meta = extraction_results.get(os.path.basename(f))
            if not meta and files_to_extract:
                 meta = extraction_results.get(f)

            self._apply_file_logic(f, meta, source_root)

    def _extract_batch_metadata(self, filenames, context):
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {} 
        # Pre-fill to ensure we have entries even if failed
        for f in filenames: results[os.path.basename(f)] = {}
        if not filenames: return results

        base_filenames = [os.path.basename(f) for f in filenames]
        rel_dir = context.get('rel_dir', "")

        for processor_name in chain:
            processor_name = processor_name.strip()
            current_results = {}
            
            if processor_name == 'guessit':
                logger.debug(f"Batch GuessIt for {len(filenames)} files")
                current_results = self._extract_batch_guessit(rel_dir, base_filenames)
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    logger.info(f"Batch LLM ({processor_name}) for {len(filenames)} files")
                    current_results = self._extract_batch_llm(rel_dir, base_filenames, config)
            elif processor_name == "cli_llm" and getattr(self.args, "llm_api_key", None):
                 config = { "api_key": self.args.llm_api_key, "base_url": self.args.llm_api_base, "model": self.args.llm_model }
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
        
        retry_failed = getattr(self.args, "retry_failed", False)
        if db_entry.get("error") and retry_failed:
            return True
            
        return False

    def _apply_file_logic(self, filepath, new_metadata, source_root):
        db_entry = self.db.get_video_entry(source_root, filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 
        
        # --- Path A: Existing File (Extraction Skipped or DB Priority) ---
        if db_entry and not new_metadata:
            final_metadata = db_entry["metadata"]
            current_db_hash = self.calculate_hash(final_metadata)
            
            # Check Hash Consistency (Detect Manual Edits)
            if db_entry.get("metadata_hash") == current_db_hash:
                # Consistent. Check Error State.
                if db_entry.get("error"):
                    # Error present, but hash matches.
                    # Unless retry_failed is on (which would have triggered extraction), we skip.
                    return 
                else:
                    # Valid entry. Ensure Link.
                    target_path = self.generate_target_path(final_metadata, filepath)
                    if target_path and not os.path.lexists(target_path):
                        create_link(filepath, target_path, soft_link)
                    
                    # Migration / Sync Check
                    if db_entry.get("source_root") != source_root:
                         self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                    return

            else:
                # Hash Mismatch -> User Edited DB manually. Trust User.
                logger.info(f"Metadata manually updated: {filepath}")
                
                # Validate manual edit
                is_valid, error_reason = self._validate_metadata(final_metadata)
                if not is_valid:
                    logger.warning(f"Manual edit invalid: {error_reason}")
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=error_reason)
                    return

                target_path = self.generate_target_path(final_metadata, filepath)
                if not target_path:
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error="Cannot generate path")
                    return

                # Conflict Check
                existing = self.db.get_file_by_target_path(target_path)
                if existing and existing != filepath:
                    logger.error(f"Target conflict: {existing}")
                    self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=f"Conflict with {existing}")
                    return

                # Re-link
                old_target = db_entry.get("target_path")
                if target_path and target_path != old_target:
                     if old_target: remove_link_and_empty_dirs(old_target)
                     create_link(filepath, target_path, soft_link)
                elif not os.path.lexists(target_path):
                     create_link(filepath, target_path, soft_link)
                
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)
                return

        # --- Path B: New Metadata (New File or Retry) ---
        if not new_metadata:
            # Extraction yielded nothing, or just empty dict
            # If DB entry exists and has error, keep it as is?
            # Or if it's new file, mark as error.
            if not db_entry:
                 self.db.update_video_entry(source_root, dst_root, filepath, {}, None, "empty_hash", error="Extraction returned empty")
            return

        logger.info(f"Processing new metadata: {filepath}")
        filename_metadata = new_metadata.copy() # Safe copy
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata # Will update in place

        is_valid, error_reason = self._validate_metadata(filename_metadata)
        if not is_valid:
            logger.warning(f"Invalid metadata: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        # FFmpeg
        logger.debug("Scanning video info...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)

        # Link
        target_path = self.generate_target_path(final_metadata, filepath)
        
        if target_path:
            existing = self.db.get_file_by_target_path(target_path)
            if existing and existing != filepath:
                logger.error(f"Target conflict: {existing}")
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=f"Conflict with {existing}")
                return

            create_link(filepath, target_path, soft_link)
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_hash, error=None)
        else:
            logger.error("No target path generated")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error="No target path")