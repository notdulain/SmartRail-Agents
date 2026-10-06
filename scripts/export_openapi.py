"""Regenerate backend/contracts/openapi.json. Run: uv run python scripts/export_openapi.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.contracts.openapi import OPENAPI_PATH, build_openapi, dump_openapi  # noqa: E402

if __name__ == "__main__":
    OPENAPI_PATH.write_text(dump_openapi(build_openapi()), encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")
