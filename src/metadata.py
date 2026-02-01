import os
import subprocess
import json

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

    except (subprocess.CalledProcessError, json.JSONDecodeError):
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
