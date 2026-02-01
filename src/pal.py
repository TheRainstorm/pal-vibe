import os
import re
import argparse
import subprocess
import json
import yaml
import hashlib
import shutil
from guessit import guessit
from openai import OpenAI

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm')

def get_video_info_ffmpeg(filepath):
    """
    Uses ffprobe to extract video stream information (width, height, frame rate, HDR).
    Returns a dictionary with video information or an empty dictionary if an error occurs.
    """
    video_info = {
        "width": None,
        "height": None,
        "frame_rate": None,
        "hdr": False
    }
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_info["width"] = stream.get("width")
                video_info["height"] = stream.get("height")

                # Get frame rate
                avg_frame_rate = stream.get("avg_frame_rate")
                if avg_frame_rate and '/' in avg_frame_rate:
                    num, den = map(int, avg_frame_rate.split('/'))
                    if den != 0:
                        video_info["frame_rate"] = round(num / den, 2)
                elif stream.get("r_frame_rate") and '/' in stream.get("r_frame_rate"):
                    num, den = map(int, stream.get("r_frame_rate").split('/'))
                    if den != 0:
                        video_info["frame_rate"] = round(num / den, 2)

                # Check for HDR
                color_primaries = stream.get("color_primaries")
                color_transfer = stream.get("color_transfer")
                color_space = stream.get("color_space")

                if (color_primaries in ["bt2020", "smpte2084"] or
                    color_transfer in ["smpte2084", "arib-std-b67"] or
                    color_space in ["bt2020nc", "bt2020cl"]):
                    video_info["hdr"] = True
                break # Only process the first video stream

    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        # print(f"Error getting video info for {filepath}: {e}")
        pass
    except FileNotFoundError:
        print("ffprobe command not found. Please ensure ffmpeg is installed and in your PATH.")
    return video_info


def scan_video_files(src_dir):
    """
    Recursively scans the source directory for video files.
    Returns a list of absolute paths to video files.
    """
    video_files = []
    for root, _, files in os.walk(src_dir):
        for file in files:
            if file.lower().endswith(VIDEO_EXTENSIONS):
                video_files.append(os.path.join(root, file))
    return video_files

def generate_version_str(metadata):
    """
    Generates a version string based on video metadata for movie linking.
    """
    parts = []
    if metadata.get("height") == 2160:
        parts.append("2160p")
    elif metadata.get("height") == 1080:
        parts.append("1080p")
    elif metadata.get("height") == 720:
        parts.append("720p")
    
    if metadata.get("hdr"):
        parts.append("HDR")
    
    return "_".join(parts) if parts else None


def generate_target_path(metadata, dst_base, original_filename, movie_folder="Movie", tv_folder="TV"):
    """
    Generates the target path for the linked file based on metadata and linking rules.
    """
    file_extension = os.path.splitext(original_filename)[1]
    title = metadata.get("title")
    year = metadata.get("year")
    
    if not title:
        print(f"Warning: Cannot generate target path, title is missing for {original_filename}")
        return None

    if metadata["type"] == "Movie":
        movie_dir = f"{title}"
        if year:
            movie_dir += f" ({year})"
        
        version_str = generate_version_str(metadata)
        
        target_filename = title
        if version_str:
            target_filename += f" - {version_str}"
        target_filename += file_extension

        return os.path.join(dst_base, movie_folder, movie_dir, target_filename)

    elif metadata["type"] == "TV":
        season = metadata.get("season", 1)
        episode = metadata.get("episode")
        ep_title = metadata.get("ep_title")
        
        series_dir = f"{title}"
        if year: # TV series can also have a year in their main folder
            series_dir += f" ({year})"

        season_dir = f"Season {season:02d}"

        episode_str = f"S{season:02d}"
        if episode:
            episode_str += f"E{episode:02d}"
        
        target_filename = episode_str
        if ep_title:
            target_filename += f" - {ep_title}"
        target_filename += file_extension

        return os.path.join(dst_base, tv_folder, series_dir, season_dir, target_filename)

    elif metadata["type"] == "WebDL":
        # WebDL is treated similarly to movies but with simpler naming
        webdl_dir = f"{title}"
        target_filename = f"{title}{file_extension}"
        return os.path.join(dst_base, "WebDL", webdl_dir, target_filename) # Using a separate 'WebDL' folder by default
    
    return None

def create_link(src_file, target_path, is_soft_link=True):
    """
    Creates a symbolic or hard link from src_file to target_path.
    Creates necessary directories if they don't exist.
    """
    target_dir = os.path.dirname(target_path)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
        print(f"Created directory: {target_dir}")

    if os.path.exists(target_path):
        # print(f"Warning: Target link already exists, skipping: {target_path}")
        return

    try:
        if is_soft_link:
            os.symlink(src_file, target_path)
            print(f"Created soft link: {target_path} -> {src_file}")
        else:
            # For hard links, source and destination must be on the same filesystem
            os.link(src_file, target_path)
            print(f"Created hard link: {target_path} -> {src_file}")
    except OSError as e:
        print(f"Error creating link from {src_file} to {target_path}: {e}")

def remove_link_and_empty_dirs(link_path):
    """
    Removes a link and then recursively removes empty parent directories.
    """
    if os.path.islink(link_path) or os.path.isfile(link_path):
        try:
            os.remove(link_path)
            print(f"Removed link/file: {link_path}")
        except OSError as e:
            print(f"Error removing link/file {link_path}: {e}")
            return

    # Recursively remove empty parent directories
    current_dir = os.path.dirname(link_path)
    while current_dir and current_dir != os.sep: # Stop at root or empty string
        try:
            # List contents, if only .DS_Store or similar, consider empty
            if not os.listdir(current_dir):
                os.rmdir(current_dir)
                print(f"Removed empty directory: {current_dir}")
                current_dir = os.path.dirname(current_dir)
            else:
                break # Not empty, stop
        except OSError as e:
            print(f"Error removing directory {current_dir}: {e}")
            break # Cannot remove, stop
        except FileNotFoundError: # Directory already removed by another process
            break

class VideoDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.data = self._load_database()

    def _load_database(self):
        if os.path.exists(self.db_path):
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_database(self):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, default_flow_style=False)

    def get_video_entry(self, src_filepath):
        return self.data.get(src_filepath)

    def calculate_metadata_hash(self, metadata):
        # Create a hash from the metadata that is *not* ffmpeg derived, as those can be re-scanned.
        # This hash is for the parts that might be manually edited or are derived from filename.
        hash_data = {
            "title": metadata.get("title"),
            "year": metadata.get("year"),
            "type": metadata.get("type"),
            "season": metadata.get("season"),
            "episode": metadata.get("episode"),
            "ep_title": metadata.get("ep_title"),
        }
        # Convert to a stable JSON string for hashing
        # Ensure consistent order by sorting keys
        stable_json = json.dumps(hash_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(stable_json.encode('utf-8')).hexdigest()

    def update_video_entry(self, src_filepath, metadata, target_path, metadata_hash):
        self.data[src_filepath] = {
            "metadata": metadata,
            "target_path": target_path,
            "metadata_hash": metadata_hash
        }
        self._save_database()

    def remove_entry(self, src_filepath):
        if src_filepath in self.data:
            del self.data[src_filepath]
            self._save_database()
            return True
        return False

# Function to extract metadata using GuessIt
def _extract_metadata_with_guessit(filename, v_type):
    guessit_options = {}
    if v_type == 0: # TV
        guessit_options['type'] = 'episode'
    elif v_type == 1: # Movie
        guessit_options['type'] = 'movie'
    # For WebDL (v_type == 2), let guessit auto-detect, then refine if needed

    guess = guessit(filename, options=guessit_options)

    metadata = {
        "title": guess.get("title"),
        "year": guess.get("year"),
        "type": None, # Will be set below based on v_type
        "season": guess.get("season", 1), # Default to 1 if not found by guessit
        "episode": guess.get("episode"),
        "ep_title": guess.get("episode_title"),
    }

    # Map guessit types to our internal types based on user's intention (v_type)
    if v_type == 0: # TV
        metadata["type"] = "TV"
    elif v_type == 1: # Movie
        metadata["type"] = "Movie"
    elif v_type == 2: # WebDL
        metadata["type"] = "WebDL"
        # For WebDL, we might want a simpler title, so override if guessit is too complex
        if guess.get("type") == "movie" and guess.get("title"):
            title_candidate = guess.get("title")
            title_candidate = re.sub(r'\[.*?\]', '', title_candidate) # Remove text in brackets
            title_candidate = re.sub(r'(\s*-\s*)?(PROPER|REPACK|UNRATED|EXTENDED|DIRECTORS CUT|THEATRICAL|LIMITED|FESTIVAL|IMAX|WEB-DL|HDR|1080p|720p|2160p|4K)\b.*', '', title_candidate, flags=re.IGNORECASE).strip()
            metadata["title"] = title_candidate if title_candidate else guess.get("title")

    # Try to get resolution and HDR from guessit if available, else rely on ffmpeg later
    if guess.get("screen_size"):
        if isinstance(guess["screen_size"], list):
            for size in guess["screen_size"]:
                if isinstance(size, int):
                    metadata["height"] = size
                    break
        elif isinstance(guess["screen_size"], int):
            metadata["height"] = guess["screen_size"]
    
    if guess.get("hdr"):
        metadata["hdr"] = True # guessit provides boolean directly

    return metadata

def _extract_metadata_from_llm(filename, v_type, api_key, api_base, model_name):
    """
    Uses an OpenAI-compatible LLM to extract metadata from a filename.
    """
    client = OpenAI(api_key=api_key, base_url=api_base)
    
    def clean_json_string(s):
        """
        清洗模型输出，提取 ```json ... ``` 中的内容，或者尝试修复常见错误
        """
        # 1. 尝试提取 Markdown 代码块中的 JSON
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if match:
            return match.group(1)
        
        # 2. 如果没有代码块，尝试寻找最外层的 {}
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            return match.group(0)
            
        return s

    expected_type = 'TV Series' if v_type == 0 else 'Movie'

    # --- 1. 优化 Prompt (增加 Few-Shot 示例) ---
    system_instruction = (
        "You are a strict video metadata extractor. "
        "Output valid JSON only. No explanation, no markdown keys."
    )

    # 构建带示例的 Prompt，小模型模仿能力比理解能力强
    prompt = f"""
    Task: Extract metadata from the filename into JSON.
    Expected Type Hint: {expected_type}

    Examples:
    Input: "The.Matrix.1999.BluRay.mkv"
    Output: {{"title": "The Matrix", "year": "1999", "type": "Movie", "season": null, "episode": null, "ep_title": null}}

    Input: "Friends.S01E02.The.One.With.The.Sonogram.mkv"
    Output: {{"title": "Friends", "year": null, "type": "TV", "season": 1, "episode": 2, "ep_title": "The One With The Sonogram"}}

    Input: "{filename}"
    Output:
    """

    metadata = {}

    try:
        chat_completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1 # 降低随机性，让小模型更稳定
        )
        
        response_content = chat_completion.choices[0].message.content
        print(f'Raw LLM Response: {response_content}')

        # --- 2. 清洗数据 ---
        cleaned_content = clean_json_string(response_content)

        # --- 3. 解析与错误处理 ---
        llm_metadata = json.loads(cleaned_content)
        
        # 映射数据（增加安全 get）
        metadata = {
            "title": llm_metadata.get("title", filename), # 如果提取不到标题，用文件名兜底
            "year": str(llm_metadata.get("year")) if llm_metadata.get("year") else None,
            "type": llm_metadata.get("type", "Movie" if v_type == 1 else "TV"),
            "season": llm_metadata.get("season", 1 if v_type == 0 else None),
            "episode": llm_metadata.get("episode"),
            "ep_title": llm_metadata.get("ep_title"),
        }
        
        print(f"Parsed Metadata: {metadata}")

    except json.JSONDecodeError as e:
        print(f"JSON Parsing Failed: {e}. Content was: {response_content}")
        # 兜底逻辑：解析失败时，至少保留文件名
        metadata = {
            "title": filename,
            "year": None,
            "type": expected_type,
            "season": 1,
            "episode": 1,
            "ep_title": None
        }

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        # 同样的兜底逻辑
        metadata = {
            "title": filename, 
            "year": None,
            "type": expected_type, 
            "season": None, 
            "episode": None, 
            "ep_title": None
        }
    return metadata

# New helper function to get only filename-derived metadata (without ffmpeg info)
def _get_filename_derived_metadata(fpath, v_type, use_llm=False, llm_api_key=None, llm_api_base=None, llm_model=None):
    filename = os.path.basename(fpath)
    metadata = None

    if use_llm:
        metadata = _extract_metadata_from_llm(filename, v_type, llm_api_key, llm_api_base, llm_model)
        if metadata and metadata.get("title"):
            # Ensure data types from LLM are consistent for hashing
            if isinstance(metadata.get("year"), int):
                metadata["year"] = str(metadata["year"])
            if isinstance(metadata.get("season"), str) and metadata["season"].isdigit():
                metadata["season"] = int(metadata["season"])
            if isinstance(metadata.get("episode"), str) and metadata["episode"].isdigit():
                metadata["episode"] = int(metadata["episode"])
            
            # Ensure 'type' is one of our recognized types
            if metadata.get("type") not in ["Movie", "TV", "WebDL"]:
                metadata["type"] = {
                    0: "TV",
                    1: "Movie",
                    2: "WebDL"
                }.get(v_type, "Movie")
        else:
            print(f"LLM extraction failed or returned insufficient data for {filename}. Falling back to GuessIt.")
            metadata = _extract_metadata_with_guessit(filename, v_type)

    if not metadata or not metadata.get("title"):
        metadata = _extract_metadata_with_guessit(filename, v_type)

    return metadata or {}

# Main function to get full metadata (filename-derived + ffmpeg) and hash
def _get_metadata_and_hash(fpath, v_type, db_instance, use_llm=False, llm_api_key=None, llm_api_base=None, llm_model=None):
    # Get filename-derived metadata first
    metadata = _get_filename_derived_metadata(fpath, v_type, use_llm, llm_api_key, llm_api_base, llm_model)

    if not metadata.get("title"):
        return {}, None # Return empty if no title can be extracted

    # Add ffmpeg info (always re-scan as it's not part of manual edits)
    metadata.update(get_video_info_ffmpeg(fpath))

    # Calculate hash for basic metadata (excluding ffmpeg info)
    temp_metadata_for_hash = metadata.copy()
    temp_metadata_for_hash.pop("width", None)
    temp_metadata_for_hash.pop("height", None)
    temp_metadata_for_hash.pop("frame_rate", None)
    temp_metadata_for_hash.pop("hdr", None)
    return metadata, db_instance.calculate_metadata_hash(temp_metadata_for_hash)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan and link video files for Jellyfin.")
    parser.add_argument("-s", "--src", required=True, help="Source directory to scan video files.")
    parser.add_argument("-d", "--dst", required=True, help="Destination directory for linked files.")
    parser.add_argument("-t", "--type", type=int, choices=[0, 1, 2], required=True,
                        help="Type of video: 0 for TV, 1 for Movie, 2 for WebDL.")
    parser.add_argument("-S", "--soft-link", action="store_true",
                        help="Create soft links instead of hard links.")
    parser.add_argument("--movie-folder", default="Movie",
                        help="Subfolder name for movies within the destination directory (default: Movie).")
    parser.add_argument("--tv-folder", default="TV",
                        help="Subfolder name for TV series within the destination directory (default: TV).")
    parser.add_argument("--db", default="pal_database.yaml",
                        help="Path to the YAML database file (default: pal_database.yaml).")
    # LLM arguments
    parser.add_argument("--use-llm", action="store_true",
                        help="Use an LLM (OpenAI compatible API) for metadata extraction.")
    parser.add_argument("--llm-api-key", help="API key for the LLM service. Required if --use-llm is true.")
    parser.add_argument("--llm-api-base", default="https://api.openai.com/v1",
                        help="Base URL for the OpenAI-compatible API (default: https://api.openai.com/v1).")
    parser.add_argument("--llm-model", default="gpt-3.5-turbo",
                        help="Model name to use for LLM metadata extraction (default: gpt-3.5-turbo).")

    args = parser.parse_args()

    if args.use_llm and not args.llm_api_key:
        parser.error("--llm-api-key is required when --use-llm is enabled.")

    # Initialize database
    db = VideoDatabase(args.db)

    print(f"Scanning for video files in {args.src} with type {args.type}...")
    found_files = scan_video_files(args.src)
    found_files_set = set(found_files)

    # Core Demand 3: Check for orphaned links and remove them
    for src_filepath_in_db in list(db.data.keys()):
        if not os.path.exists(src_filepath_in_db):
            print(f"Source file {src_filepath_in_db} from DB not found. Removing associated link and DB entry.")
            entry = db.get_video_entry(src_filepath_in_db)
            if entry and "target_path" in entry:
                remove_link_and_empty_dirs(entry["target_path"])
            db.remove_entry(src_filepath_in_db)

    if not found_files:
        print("No video files found.")
    
    for filepath in found_files:
        current_metadata = {}
        metadata_hash = None
        
        db_entry = db.get_video_entry(filepath)

        if db_entry:
            stored_metadata = db_entry["metadata"]
            stored_hash = db_entry["metadata_hash"]

            cur_hash = db.calculate_metadata_hash(stored_metadata)

            if stored_hash == cur_hash:
                continue # No changes in filename-derived metadata, skip re-processing
            else:
                print(f"Metadata hash mismatch for {filepath}. Use value handlely setted, will relinking")
                db_entry["metadata_hash"] = cur_hash
        else: # No entry in DB, new file
            print(f"New file found: {filepath}. Extracting all metadata (including ffmpeg).")
            current_metadata, metadata_hash = _get_metadata_and_hash(
                filepath, args.type, db, args.use_llm, args.llm_api_key, args.llm_api_base, args.llm_model
            )

        if current_metadata.get("title"):
            print(f"Processing File: {filepath}, Extracted Metadata: {current_metadata}")
            target_path = generate_target_path(
                current_metadata, args.dst, os.path.basename(filepath),
                movie_folder=args.movie_folder, tv_folder=args.tv_folder
            )
            
            if target_path:
                # If target path changed due to metadata edit, remove old link first
                if db_entry and db_entry.get("target_path") and db_entry["target_path"] != target_path:
                    print(f"Target path changed for {filepath}. Removing old link: {db_entry["target_path"]}")
                    remove_link_and_empty_dirs(db_entry["target_path"])

                create_link(filepath, target_path, args.soft_link)
                # Update database with new metadata and target path
                db.update_video_entry(filepath, current_metadata, target_path, metadata_hash)
            else:
                print(f"Skipping {filepath}: Could not generate target path.")
        else:
            print(f"Skipping {filepath}: Could not extract title metadata.")
