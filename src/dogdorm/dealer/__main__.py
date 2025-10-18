from .dealer import *

uvicorn.run(
    app,
    host="*",
    port=8000,
    reload=False
)