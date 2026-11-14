"""Export the backend OpenAPI schema (no server or database needed)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from am.main import create_app  # noqa: E402

out = Path(sys.argv[1])
out.write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
print(f"wrote {out}")
