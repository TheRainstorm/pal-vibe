import logging
import sys

def setup_logging(verbose=False, log_level=None):
    # Determine target level for OUR code
    if log_level:
        target_level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        target_level = logging.DEBUG if verbose else logging.INFO

    # 1. Configure Root Logger to WARNING (silence everyone else)
    logging.basicConfig(
        level=logging.WARNING, 
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # 2. Configure "src" Logger to desired level
    # This assumes all our loggers are created like get_logger("src.xxx") or get_logger(__name__) inside src package
    my_logger = logging.getLogger("src")
    my_logger.setLevel(target_level)
    
    # Ensure messages propagate up to root handler (default behavior), 
    # but since root filters by level, we might need to be careful if root level is higher than src level.
    # Actually, logging.basicConfig sets the handler on the root logger.
    # The handler's level defaults to NOTSET (process everything).
    # The logger's effective level determines if it passes to handler.
    
    # So: Root Logger = WARNING.
    # src Logger = DEBUG.
    # src.debug(...) -> src allows it -> propagates to Root -> Root allows it? NO.
    # If Root Logger level is WARNING, it will filter out DEBUG messages even if they propagated from a child.
    
    # CORRECTION: We need to set the HANDLER level to DEBUG, but the ROOT LOGGER level to WARNING.
    # And then set 'src' LOGGER to DEBUG.
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING) # Block noisy libs
    
    # Reset handlers to ensure clean state
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
            
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG) # Handler allows everything
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    
    root_logger.addHandler(handler)
    
    # Set our package's logger to the desired level
    logging.getLogger("src").setLevel(target_level)
    # Also set __main__ logger just in case
    logging.getLogger("__main__").setLevel(target_level)

def get_logger(name):
    return logging.getLogger(name)
