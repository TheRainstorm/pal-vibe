# Pal-Vibe Agent Guide

## Build/Run Commands

### Running the Application

```bash
# Run directly with CLI args
python3 src/pal.py -s "/path/to/source" -d "/path/to/dest" -t movie -S

# Run with config file
python3 src/pal.py -c config.yaml

# Run in monitor mode (watch for file changes)
python3 src/pal.py -c config.yaml --monitor

# Start web UI
./start_web.sh
# or
export PYTHONPATH=$PYTHONPATH:$(pwd)
python src/web/server.py
```

### Testing

Currently no formal test suite exists. To test manually:

```bash
# Test with verbose logging
python3 src/pal.py -c config.yaml -v

# Test a specific task by modifying config.yaml
python3 src/pal.py -c config.yaml

# Test web UI locally
./start_web.sh
# Then open http://localhost:8000
```

### Dependencies

Install requirements:
```bash
pip install pyyaml guessit openai fastapi uvicorn python-multipart watchdog
```

## Code Style Guidelines

### Imports

Order: standard library → third-party → local imports (grouped together)

```python
import os
import argparse
import json

from guessit import guessit
from openai import OpenAI
from fastapi import FastAPI

from src.database import VideoDatabase
from src.logger import get_logger
from src.metadata import scan_video_files
```

### Naming Conventions

- **Variables/Functions**: snake_case (`process_files`, `file_path`)
- **Classes**: PascalCase (`BaseVideoPlugin`, `VideoDatabase`)
- **Constants**: UPPER_SNAKE_CASE (`VIDEO_EXTENSIONS`)
- **Private methods**: underscore prefix (`_extract_batch_metadata`)

### Type Hints

Type hints are optional but encouraged for complex functions:

```python
from typing import List, Dict, Optional, Any

def process_files(self, filepaths: List[str], source_root: str) -> None:
    ...
```

### Error Handling

Use try/except with logging for expected failures:

```python
try:
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    logger.error(f"Command failed: {e}")
    return None
except FileNotFoundError:
    logger.error("Command not found. Please install dependencies.")
    return None
```

### Logging

Initialize logger at module level:

```python
from src.logger import get_logger

logger = get_logger(__name__)
```

Log levels:
- `logger.debug()` - Detailed debugging info
- `logger.info()` - General informational messages
- `logger.warning()` - Unexpected but recoverable issues
- `logger.error()` - Error conditions

### File Paths

Always normalize paths with `os.path.normpath()` for cross-platform consistency:

```python
src_path = os.path.normpath(src_path)
dst_path = os.path.join(self.args.dst, sub_folder, filename)
```

### String Formatting

Use f-strings for string formatting:

```python
logger.info(f"Processing {len(files)} files")
target_path = os.path.join(dst, f"{title} ({year}){ext}")
```

### JSON/YAML Handling

- Use `yaml.safe_load()` and `yaml.safe_dump()` for YAML
- Use `json.dumps()` with `sort_keys=True` for consistent hashing
- Handle encoding explicitly: `encoding='utf-8'`

```python
import json
import yaml

with open(path, 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

hash_data = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
```

### Docstrings

Use simple docstrings for classes and key methods:

```python
def create_link(src_file, target_path, is_soft_link=True):
    """
    Creates a symbolic or hard link from src_file to target_path.
    Creates necessary directories if they don't exist.
    """
```

### Constants

Define module-level constants at the top:

```python
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm')
```

### Configuration

- Config files use YAML format
- Use `getattr(self.args, 'key', default)` for optional config values
- Always provide sensible defaults

```python
batch_size = getattr(self.args, 'batch_size', 26)
soft_link = getattr(self.args, 'soft_link', True)
```

### Plugin System

Plugins inherit from `BaseVideoPlugin` and implement:
- `get_type_name()` - Return string type identifier
- `_map_guessit_to_metadata(guess)` - Map GuessIt output to metadata dict
- `_get_batch_llm_prompt(context, filenames)` - Generate LLM prompt
- `generate_target_path(metadata, filepath)` - Generate destination path
- `_validate_metadata(metadata)` - Return (bool, error_msg) tuple

### Database

Database is YAML-based, accessed via `VideoDatabase` class:
- `get_video_entry(src_root, src_filepath)` - Retrieve entry
- `update_video_entry(...)` - Create/update entry
- `remove_entry(src_root, src_filepath)` - Delete entry
- Database auto-saves on modification

### File System Operations

- Use `os.makedirs(path, exist_ok=True)` for creating directories
- Use `os.path.lexists()` for checking if paths exist (includes symlinks)
- Use `os.path.relpath()` for calculating relative paths
- Use `os.path.splitext()` to get file extensions
