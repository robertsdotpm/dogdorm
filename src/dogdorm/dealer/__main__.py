import uvicorn
from .dealer import *

for name in ("httpx", "httpcore", "aiosqlite", "sqlite3", "core"):
    logging.getLogger(name).setLevel(logging.WARNING)

uvicorn.run(
    app,
    host=DEALER_BIND_HOST,
    port=DEALER_PORT,
    reload=False,
    log_level="warning"
)