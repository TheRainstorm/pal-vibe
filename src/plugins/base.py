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
       |--> _needs_extraction(file)?
       |    |
       |    +-> No -> _process_existing_db_entry -> (Skip extraction)
       |
       +--> [*] _group_files(filepaths) -> batches
       |
       v
    Loop over batches:
       +--> _process_batch(batch)
        |
        +--> [*] _extract_batch_metadata(files) -> {path: meta}
        |
        v
       Loop over files in batch:
          _process_new_metadata(...)
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
        filepaths: file paths relative to source_root
        """

        files_to_extract = []
        for f in filepaths:
            db_entry = self.db.get_video_entry(source_root, f)
            if self._needs_extraction(f, db_entry):
                files_to_extract.append(f)
            else:
                # process existing entry
                self._process_existing_db_entry(f, db_entry, source_root)

        # process new files
        batches = self._group_files(files_to_extract)
        logger.info(f"Processing {len(files_to_extract)} files in {len(batches)} batches...")
        
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

    def _get_batch_llm_prompt(self, filenames):
        """
        Return the prompt for batch LLM extraction.
        filenames: list of strings to be included in prompt.
        """
        raise NotImplementedError
    
    def _get_guessit_options(self): return {}
    
    def _map_guessit_to_metadata(self, guess):
        raise NotImplementedError

    def _check_and_fix_metadata(self, meta):
        return False
    
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
        
        results = {}
        if files:
            results = self._extract_batch_metadata(files, context)
        
        for f in files:
            meta = results.get(f) 
            self._process_new_metadata(f, meta, source_root)

    def _extract_batch_metadata(self, filenames, context):
        """
        return { file: metadata }
        metadata can be None if extraction failed.
        """
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})
        
        results = {} 
        # Pre-fill to ensure we have entries even if failed
        for f in filenames: results[f] = {}
        if not filenames: return results

        for processor_name in chain:
            processor_name = processor_name.strip()
            current_results = {} # { abs_path: meta }
            
            logger.info(f"Batch ({processor_name}) for {len(filenames)} files, context: {context}")
            if processor_name == 'guessit':
                current_results = self._extract_batch_guessit(context, filenames)
            elif processor_name in providers:
                config = providers[processor_name]
                if config.get("type") == "llm":
                    current_results = self._extract_batch_llm(context, filenames, config)

            if current_results:
                # Update main results with valid entries
                succ, ratio = self._validate_batch(current_results)
                if succ:
                    for k, v in current_results.items():
                        self._check_and_fix_metadata(v)
                        results[k] = v
                    break
            logger.info(f"{processor_name}: valid ratio {ratio*100:.1f}%")
        return results

    def _extract_batch_guessit(self, context, filenames):
        results = {}
        for f in filenames:
            if 'rel_dir' in context:
                fname = os.path.relpath(f, context['rel_dir'])
            else:
                fname = os.path.basename(f)
            options = self._get_guessit_options()
            guess = guessit(fname, options=options)
            results[f] = self._map_guessit_to_metadata(guess)
        return results

    def _extract_batch_llm(self, context, filenames, config):
        # Default LLM implementation: Uses basenames in prompt
        # Map: Basename -> Abs Path (rel to source_root)
        name_map = {}
        base_filenames = []
        for f in filenames:
            if 'rel_dir' in context:
                fname = os.path.relpath(os.path.relpath(f, context['rel_dir']))
            else:
                fname = os.path.basename(f)
            name_map[fname] = f
            base_filenames.append(fname)
        
        prompt = self._get_batch_llm_prompt(context, base_filenames)
        if not prompt: 
            return {}

        response_data = self._call_llm(config, prompt)
        logger.debug(f"{prompt=}\n\n{response_data=}")
        if not isinstance(response_data, dict): return {}

        # Map keys back to abs paths
        results = {}
        for fname, meta in response_data.items():
            if fname in name_map:
                # fix type
                meta['type'] = self.get_type_name()
                results[name_map[fname]] = meta
            else:
                logger.warning(f"LLM returned unexpected filename key: {fname}")
        
        return results

    def _call_llm(self, config, prompt):
        api_key = config.get("api_key")
        api_base = config.get("base_url")
        model = config.get("model")
        
        if not api_key:
            logger.error("Missing api_key for LLM provider.")
            return None

        client = OpenAI(api_key=api_key, base_url=api_base)
        try:
            chat_completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "text"}
            )
            content = chat_completion.choices[0].message.content
            # Locate JSON content if wrapped in markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
            
            return json.loads(content)
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return None

    def _validate_batch(self, results):
        if not results: return False, 0
        valid_count = 0
        total_count = len(results)
        for meta in results.values():
            if self._validate_metadata(meta)[0]:
                valid_count += 1
        ratio = valid_count / total_count if total_count > 0 else 0
        return ratio >= 0.5, ratio

    def _needs_extraction(self, filepath, db_entry):
        if not db_entry: return True
        
        retry_failed = getattr(self.args, "retry_failed", False)
        if db_entry.get("error") and retry_failed:
            return True
        
        return False

    def _process_existing_db_entry(self, filepath, db_entry, source_root):
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 
        final_metadata = db_entry["metadata"]
        current_db_hash = self.calculate_hash(final_metadata)
        
        if self._check_and_fix_metadata(final_metadata):
            logger.warning(f"Fixed metadata for {filepath}, somewhere (webui/human) write invalid metadata to db")

        if db_entry.get("metadata_hash") == current_db_hash:
            # Consistent. Check Error State.
            if db_entry.get("error"):
                return
            else:
                # Valid entry. Ensure Link.
                target_path = self.generate_target_path(final_metadata, filepath)
                if target_path and not os.path.lexists(target_path):
                    logger.info(f"Link missing, recreating: {target_path}")
                    create_link(filepath, target_path, soft_link)
                
                # Migration / Sync Check
                logger.debug(f"No change detected for: {filepath}")
                return
        else:
            # Hash Mismatch -> User Edited DB manually. Trust User.
            logger.info(f"Metadata changed (corrected by human), re-processing: {filepath}")
            
            is_valid, error_reason = self._validate_metadata(final_metadata)
            if not is_valid:
                logger.warning(f"Meta after edit is invalid: {error_reason}")
                old_target = db_entry.get("target_path")
                if old_target:
                    logger.info(f"Removing old link due to invalid metadata: {old_target}")
                    remove_link_and_empty_dirs(old_target)
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=error_reason)
                return

            target_path = self.generate_target_path(final_metadata, filepath)
            if not target_path:
                logger.error(f"Cannot generate target path for {filepath}")
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error="Cannot generate path")
                return

            existing = self.db.get_file_by_target_path(target_path)
            if existing and existing != filepath:
                logger.error(f"Target path conflict: {target_path} used by {existing}")
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_db_hash, error=f"Conflict with {existing}")
                return

            old_target = db_entry.get("target_path")
            if target_path and target_path != old_target:
                logger.info(f"Target path changed. Relinking to: {target_path}")
                if old_target: remove_link_and_empty_dirs(old_target)
                create_link(filepath, target_path, soft_link)
            elif not os.path.lexists(target_path):
                logger.info(f"Link missing, creating: {target_path}")
                create_link(filepath, target_path, soft_link)
            
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_db_hash, error=None)

    def _process_new_metadata(self, filepath, new_metadata, source_root):
        logger.info(f"Processing new file: {filepath}")
        if not new_metadata:
            logger.warning(f'Extraction returned empty')
            # Extraction yielded nothing
            if not self.db.get_video_entry(source_root, filepath):
                 self.db.update_video_entry(source_root, getattr(self.args, "dst"), filepath, {}, None, "empty", error="Extraction returned empty")
            return

        logger.debug(f"filename metadata: {new_metadata}")
        
        filename_metadata = new_metadata
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata

        # Check validation
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        if not is_valid:
            logger.warning(f"Invalid metadata: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        # FFmpeg scan
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)
        logger.debug(f"ffmpeg_info: {ffmpeg_info}")

        # Generate Link
        target_path = self.generate_target_path(final_metadata, filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        dst_root = getattr(self.args, "dst") 

        if target_path:
            existing = self.db.get_file_by_target_path(target_path)
            if existing and existing != filepath:
                logger.error(f"Target path conflict: {target_path} used by {existing}")
                self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=f"Conflict with {existing}")
                return

            create_link(filepath, target_path, soft_link)
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, target_path, current_hash, error=None)
        else:
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error="No target path")