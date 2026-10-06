"""Backend on FakeRuntime (no OpenCode, no model calls) for UI development and demos.

    SMARTRAIL_PORT=8773 SMARTRAIL_DATA_DIR=.data uv run python scripts/dev_fake_backend.py

Use FAKE_CHUNK_DELAY=0.05 to make streaming visible.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from backend.config import AppConfig, load_config  # noqa: E402
from backend.contracts.fake_runtime import FakeRuntime  # noqa: E402
from backend.main import build_app  # noqa: E402

if __name__ == "__main__":
    cfg = load_config()
    config = AppConfig(data_dir=cfg.data_dir, port=cfg.port)
    runtime = FakeRuntime(chunk_delay=float(os.environ.get("FAKE_CHUNK_DELAY", "0.03")))
    print(f"Fake backend on http://127.0.0.1:{config.port}  data={config.data_dir}")
    uvicorn.run(build_app(config, runtime), host="127.0.0.1", port=config.port, log_level="warning")
