"""python -m src.serving  -> the API (and the built dashboard) on http://127.0.0.1:8000"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.serving.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")),
                log_level="warning")
