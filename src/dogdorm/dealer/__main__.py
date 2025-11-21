import uvicorn
from .dealer import *

for name in ("httpx", "httpcore", "aiosqlite", "sqlite3", "core"):
    logging.getLogger(name).setLevel(logging.WARNING)

uvicorn.run(
    app,
    host="*",
    port=8000,
    reload=False,
    log_level="warning"
)