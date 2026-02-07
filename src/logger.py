import logging
import sys

# ANSI Colors
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: MAGENTA,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, WHITE)
        
        # Format timestamp
        asctime = self.formatTime(record, self.datefmt)
        
        # Format levelname (fixed width 8)
        levelname = f"{record.levelname:<8}"
        
        # Format name (truncate or pad, say width 20)
        name = record.name
        if len(name) > 20:
            name = name[:17] + "..."
        name = f"{name:<20}"
        
        # Build message
        msg = record.getMessage()
        
        return f"{color}{asctime} | {levelname} | {name} | {msg}{RESET}"

def setup_logging(verbose=False, log_level=None):
    if log_level:
        target_level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        target_level = logging.DEBUG if verbose else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING) 
    
    # Reset handlers
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
            
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG) 
    
    # Use custom formatter
    formatter = ColoredFormatter(datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    
    root_logger.addHandler(handler)
    
    logging.getLogger("src").setLevel(target_level)
    logging.getLogger("__main__").setLevel(target_level)

    # Silence libs
    logging.getLogger("guessit").setLevel(logging.WARNING)
    logging.getLogger("rebulk").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)

def get_logger(name):
    return logging.getLogger(name)