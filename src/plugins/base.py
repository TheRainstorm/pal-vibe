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

    def extract_filename_metadata(self, filepath):
        filename = os.path.basename(filepath)
        chain = getattr(self.args, 'chain', ['guessit'])
        providers = getattr(self.args, 'providers', {})

        for processor_name in chain:
            processor_name = processor_name.strip()
            metadata = None
            
            if processor_name == 'guessit':
                logger.debug(f"Extracting metadata using GuessIt for: {filename}")
                metadata = self._extract_guessit(filename)
            elif processor_name in providers:
                logger.debug(f"Extracting metadata using Provider '{processor_name}' for: {filename}")
                provider_config = providers[processor_name]
                if provider_config.get("type") == "llm":
                    metadata = self._extract_llm(filename, provider_config)
                    logger.info(f"{processor_name}: {metadata}")
            else:
                logger.warning(f"Unknown processor '{processor_name}' in chain.")

            is_valid, _ = self._validate_metadata(metadata)
            if is_valid:
                logger.debug(f"Successfully extracted metadata with {processor_name}: {metadata}")
                return metadata
        
        logger.warning(f"All processors failed to extract valid metadata for: {filename}")
        return metadata if metadata else {}

    def _extract_guessit(self, filename):
        options = self._get_guessit_options()
        guess = guessit(filename, options=options)
        return self._map_guessit_to_metadata(guess)

    def _extract_llm(self, filename, config):
        api_key = config.get("api_key")
        api_base = config.get("base_url", "https://api.openai.com/v1")
        model = config.get("model", "gpt-3.5-turbo")
        
        if not api_key:
            logger.error("Missing api_key for LLM provider.")
            return None

        client = OpenAI(api_key=api_key, base_url=api_base)
        prompt = self._get_llm_prompt(filename)
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
            
            return json.loads(content)
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return None

    def _get_guessit_options(self):
        return {}

    def _get_llm_prompt(self, filename):
        return f"Extract metadata from '{filename}' as JSON."

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
        filename_metadata = self.extract_filename_metadata(filepath)
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata.copy()

        # Step 3: Check validation
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        
        if not is_valid:
            logger.warning(f"Invalid metadata for {filepath}: {error_reason}")
            self.db.update_video_entry(source_root, dst_root, filepath, final_metadata, None, current_hash, error=error_reason)
            return

        # Step 4: FFmpeg scan
        logger.info(f"Scanning technical info...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
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