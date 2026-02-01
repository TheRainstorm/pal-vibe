import os
import json
import hashlib
from guessit import guessit
from openai import OpenAI
from src.metadata import get_video_info_ffmpeg
from src.link_manager import create_link, remove_link_and_empty_dirs

class BaseVideoPlugin:
    def __init__(self, db, args):
        self.db = db
        self.args = args

    def get_type_name(self):
        raise NotImplementedError

    def extract_filename_metadata(self, filepath):
        """
        Extracts metadata purely from the filename (using GuessIt or LLM).
        Must return a dictionary.
        """
        filename = os.path.basename(filepath)
        # Check args for LLM usage - args might be an object or dict depending on implementation
        use_llm = getattr(self.args, 'use_llm', False)
        
        if use_llm:
            metadata = self._extract_llm(filename)
            # Basic validation: check if metadata isn't None
            if metadata: 
                return metadata
            print(f"LLM extraction failed for {filename}. Falling back to GuessIt.")
        
        return self._extract_guessit(filename)

    def _extract_guessit(self, filename):
        # Default implementation, can be overridden
        options = self._get_guessit_options()
        guess = guessit(filename, options=options)
        return self._map_guessit_to_metadata(guess)

    def _extract_llm(self, filename):
        api_key = getattr(self.args, 'llm_api_key', None)
        api_base = getattr(self.args, 'llm_api_base', 'https://api.openai.com/v1')
        model = getattr(self.args, 'llm_model', 'gpt-3.5-turbo')
        
        client = OpenAI(api_key=api_key, base_url=api_base)
        prompt = self._get_llm_prompt(filename)
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
            print(f"LLM Error: {e}")
            return None

    def _get_guessit_options(self):
        return {}

    def _get_llm_prompt(self, filename):
        return f"Extract metadata from '{filename}' as JSON."

    def _map_guessit_to_metadata(self, guess):
        return dict(guess)

    def _validate_metadata(self, metadata):
        # Default validation: title is required
        if not metadata or not metadata.get("title"):
            return False, "Missing title"
        return True, "OK"

    def generate_target_path(self, metadata, filepath):
        raise NotImplementedError

    def calculate_hash(self, metadata):
        # Calculate hash based on filename-derived fields only
        # Subclasses should define which fields are important
        stable_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()

    def process_file(self, filepath, source_root):
        db_entry = self.db.get_video_entry(filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        
        # Step 1: Check against DB
        if db_entry:
            final_metadata = db_entry["metadata"]
            
            # Recalculate hash based on what is currently in the DB
            # This detects if the user manually edited the DB file
            current_db_hash = self.calculate_hash(final_metadata)
            
            if db_entry.get("metadata_hash") == current_db_hash:
                # No manual changes in DB.
                # Check if it was previously an error
                if db_entry.get("error"):
                    # It was an error, and user hasn't touched it. 
                    # We could retry logic here if we wanted to be robust, 
                    # but for now, we just skip it as per "user hasn't fixed it"
                    # UNLESS the logic code changed? No, assuming code stable.
                    # Actually, let's re-validate just in case our code changed logic.
                    is_valid, error_reason = self._validate_metadata(final_metadata)
                    if not is_valid:
                        # Still invalid
                        return 
                    
                    # If it became valid (e.g. code update), we proceed to re-link below.
                    print(f"File {filepath} previously had error but now seems valid. Retrying.")
                else:
                    # Valid file, unchanged. Just ensure link exists.
                    target_path = self.generate_target_path(final_metadata, filepath)
                    if not target_path: return # Should not happen for valid files

                    if not os.path.exists(db_entry.get("target_path", "")):
                        create_link(filepath, target_path, soft_link)
                    
                    # Migration check
                    if db_entry.get("source_root") != source_root:
                         self.db.update_video_entry(filepath, final_metadata, target_path, current_db_hash, source_root)
                    return 

            else:
                # User manually edited the DB metadata
                print(f"Metadata changed for {filepath} (corrected by human), re-processing.")
                # We trust the user's data. 
                # Check if it's now valid
                is_valid, error_reason = self._validate_metadata(final_metadata)
                
                if not is_valid:
                    print(f"User edit for {filepath} is still invalid: {error_reason}")
                    self.db.update_video_entry(filepath, final_metadata, None, current_db_hash, source_root, error=error_reason)
                    return

                # Valid now!
                target_path = self.generate_target_path(final_metadata, filepath)
                if not target_path:
                    self.db.update_video_entry(filepath, final_metadata, None, current_db_hash, source_root, error="Cannot generate target path")
                    return

                # Check conflict
                existing_owner = self.db.get_file_by_target_path(target_path)
                if existing_owner and existing_owner != filepath:
                    print(f"Target path conflict for {filepath}. Already used by {existing_owner}.")
                    self.db.update_video_entry(filepath, final_metadata, None, current_db_hash, source_root, error=f"Target path conflict with {existing_owner}")
                    return

                # Re-link
                if target_path and target_path != db_entry.get("target_path"):
                     print(f"Target path changed for {filepath}. Relinking.")
                     remove_link_and_empty_dirs(db_entry.get("target_path"))
                     create_link(filepath, target_path, soft_link)
                elif not os.path.exists(db_entry.get("target_path", "")):
                     create_link(filepath, target_path, soft_link)
                
                # Update DB, clear error
                self.db.update_video_entry(filepath, final_metadata, target_path, current_db_hash, source_root, error=None)
                return 

        # Step 2: New File - Extract filename metadata
        filename_metadata = self.extract_filename_metadata(filepath)
        
        # Calculate hash of filename metadata immediately
        current_hash = self.calculate_hash(filename_metadata)
        final_metadata = filename_metadata.copy()

        # Step 3: Check validation
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        
        if not is_valid:
            print(f"Failed to validate metadata for {filepath}: {error_reason}. Saving error state.")
            # Save incomplete metadata to DB with error flag
            self.db.update_video_entry(filepath, final_metadata, None, current_hash, source_root, error=error_reason)
            return

        # Step 4: FFmpeg scan (Only for valid new files)
        print(f"Scanning technical info for {filepath}...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)

        # Step 5: Generate Link
        target_path = self.generate_target_path(final_metadata, filepath)
        
        if target_path:
            # Check for conflict
            existing_owner = self.db.get_file_by_target_path(target_path)
            if existing_owner and existing_owner != filepath:
                print(f"Target path conflict for {filepath}. Already used by {existing_owner}.")
                self.db.update_video_entry(filepath, final_metadata, None, current_hash, source_root, error=f"Target path conflict with {existing_owner}")
                return

            create_link(filepath, target_path, soft_link)
            self.db.update_video_entry(filepath, final_metadata, target_path, current_hash, source_root, error=None)
        else:
            print(f"Could not generate target path for {filepath}")
            self.db.update_video_entry(filepath, final_metadata, None, current_hash, source_root, error="Could not generate target path")
