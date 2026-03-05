import logging
import os
from logging.handlers import RotatingFileHandler
from contextvars import ContextVar

# ContextVar to store the current request/correlation ID
request_id_var: ContextVar[str] = ContextVar("request_id", default="N/A")

# Define base directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "logs")

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# Logger Configuration
LOG_FILE = os.path.join(LOGS_DIR, "app.log")
TRACE_FILE = os.path.join(LOGS_DIR, "trace.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

def get_logger(name: str):
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    if logger.hasHandlers():
        logger.handlers.clear()

    # Enhanced Formatter with RID (Request ID)
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s [RID:%(rid)s] [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Inject RID into every record
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.rid = request_id_var.get()
        return record
    logging.setLogRecordFactory(record_factory)

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Main App Log
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Dedicated Trace Log (more verbose, always DEBUG)
    trace_handler = RotatingFileHandler(TRACE_FILE, maxBytes=20*1024*1024, backupCount=3)
    trace_handler.setFormatter(formatter)
    trace_handler.setLevel(logging.DEBUG)
    logger.addHandler(trace_handler)

    return logger

# Default logger
logger = get_logger("dbt_mcp_agent")
