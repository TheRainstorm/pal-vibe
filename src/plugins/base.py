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
            if self._validate_metadata(metadata):
                return metadata
            print(f"LLM extraction failed or invalid for {filename}. Falling back to GuessIt.")
        
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
        # Feat 1: Skip if in error list
        if self.db.is_error_file(filepath):
            # Check if file still exists, if not, clean up error list
            if not os.path.exists(filepath):
                 del self.db.data["error_files"][filepath]
                 self.db._save_database()
            return

        db_entry = self.db.get_video_entry(filepath)
        soft_link = getattr(self.args, 'soft_link', True)
        
        # Step 1: Check against DB
        if db_entry:
            final_metadata = db_entry["metadata"]
            
            # Recalculate hash based on what is currently in the DB
            # This detects if the user manually edited the DB file
            current_db_hash = self.calculate_hash(final_metadata)
            
            if db_entry["metadata_hash"] == current_db_hash:
                # No manual changes in DB, and file exists in DB.
                # We assume the data is correct and valid.
                # Just ensure the link exists.
                target_path = self.generate_target_path(final_metadata, filepath)
                if not target_path:
                     print(f"Skipping {filepath}: Cannot generate target path from cached metadata.")
                     return

                # Check for conflict (Target path collision)
                existing_owner = self.db.get_file_by_target_path(target_path)
                if existing_owner and existing_owner != filepath:
                    print(f"Target path conflict for {filepath}. Already used by {existing_owner}.")
                    self.db.add_error_file(filepath, f"Target path conflict with {existing_owner}")
                    return

                if not os.path.exists(db_entry.get("target_path", "")):
                    # Link missing? Recreate
                    create_link(filepath, target_path, soft_link)
                
                # Check if we need to update source_root if it was missing or different (migration scenario)
                if db_entry.get("source_root") != source_root:
                     self.db.update_video_entry(filepath, final_metadata, target_path, current_db_hash, source_root)

                return # Done for this file
            else:
                # User manually edited the DB metadata
                print(f"Metadata changed for {filepath} (corrected by human), re-linking.")
                
                # Re-generate target path with user-corrected metadata
                target_path = self.generate_target_path(final_metadata, filepath)
                
                if not target_path:
                    print(f"Failed to generate target path for {filepath} using updated metadata.")
                    # Keep DB entry as is, but maybe user made a mistake? 
                    # We don't move to error list immediately if it was already in valid files list, 
                    # but maybe we should print a warning.
                    return

                # Check for conflict
                existing_owner = self.db.get_file_by_target_path(target_path)
                if existing_owner and existing_owner != filepath:
                    print(f"Target path conflict for {filepath}. Already used by {existing_owner}.")
                    # If conflict, we cannot link. Move to error list? Or just skip?
                    # Since user manually edited this, let's treat it as an error so they see it.
                    self.db.add_error_file(filepath, f"Target path conflict with {existing_owner}")
                    return

                if target_path and target_path != db_entry.get("target_path"):
                     print(f"Target path changed for {filepath}. Relinking.")
                     remove_link_and_empty_dirs(db_entry.get("target_path"))
                     create_link(filepath, target_path, soft_link)
                elif not os.path.exists(db_entry.get("target_path", "")):
                     # Link missing? Recreate
                     if target_path:
                        create_link(filepath, target_path, soft_link)
                
                # Update DB with new hash so we don't trigger this again next time
                self.db.update_video_entry(filepath, final_metadata, target_path, current_db_hash, source_root)
                return # Done for this file

        # Step 2: New File - Extract filename metadata
        filename_metadata = self.extract_filename_metadata(filepath)
        
        # Check if valid
        is_valid, error_reason = self._validate_metadata(filename_metadata)
        if not is_valid:
            print(f"Failed to extract metadata for {filepath}: {error_reason}. Adding to error list.")
            self.db.add_error_file(filepath, error_reason)
            return

        # Calculate hash of filename metadata
        current_hash = self.calculate_hash(filename_metadata)
        
        final_metadata = filename_metadata.copy()
        
        # Step 3: FFmpeg scan (Only for new files)
        print(f"Scanning technical info for {filepath}...")
        ffmpeg_info = get_video_info_ffmpeg(filepath)
        final_metadata.update(ffmpeg_info)

        # Step 4: Generate Link
        target_path = self.generate_target_path(final_metadata, filepath)
        
        if target_path:
            # Check for conflict
            existing_owner = self.db.get_file_by_target_path(target_path)
            if existing_owner and existing_owner != filepath:
                print(f"Target path conflict for {filepath}. Already used by {existing_owner}.")
                self.db.add_error_file(filepath, f"Target path conflict with {existing_owner}")
                return

            # Defensive check for old links
            if db_entry and db_entry.get("target_path") and db_entry["target_path"] != target_path:
                remove_link_and_empty_dirs(db_entry["target_path"])
            
            create_link(filepath, target_path, soft_link)
            self.db.update_video_entry(filepath, final_metadata, target_path, current_hash, source_root)
        else:
            print(f"Could not generate target path for {filepath}")
            self.db.add_error_file(filepath, "Could not generate target path")