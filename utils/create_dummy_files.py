import os
import argparse

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm')

def create_dummy_structure(src_dir, dst_dir):
    """
    Recursively copies the file structure from src_dir to dst_dir,
    creating 0-byte dummy files for actual files and replicating directories.
    Only copies files with extensions defined in VIDEO_EXTENSIONS.
    """
    if not os.path.exists(src_dir):
        print(f"Source directory does not exist: {src_dir}")
        return

    print(f"Creating dummy structure from '{src_dir}' to '{dst_dir}'...")
    for root, dirs, files in os.walk(src_dir):
        # Get the relative path from the source directory
        rel_path = os.path.relpath(root, src_dir)
        current_dst_dir = os.path.join(dst_dir, rel_path)

        # Create corresponding directories in the destination
        os.makedirs(current_dst_dir, exist_ok=True)

        for file in files:
            if file.lower().endswith(VIDEO_EXTENSIONS):
                src_file_path = os.path.join(root, file)
                dst_file_path = os.path.join(current_dst_dir, file)
                
                # Create a 0-byte dummy file
                with open(dst_file_path, 'a') as f:
                    os.utime(dst_file_path, None) # Update modification time, ensuring 0 bytes
                print(f"Created dummy file: {dst_file_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a dummy directory structure with 0-byte video files.")
    parser.add_argument("-s", "--src", required=True, help="Source directory to mimic.")
    parser.add_argument("-d", "--dst", required=True, help="Destination directory for the dummy structure.")
    args = parser.parse_args()

    create_dummy_structure(args.src, args.dst)
    print("Dummy structure creation complete.")
