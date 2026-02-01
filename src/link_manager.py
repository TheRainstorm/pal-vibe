import os

def create_link(src_file, target_path, is_soft_link=True):
    """
    Creates a symbolic or hard link from src_file to target_path.
    Creates necessary directories if they don't exist.
    """
    target_dir = os.path.dirname(target_path)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
        print(f"Created directory: {target_dir}")

    if os.path.lexists(target_path):
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
