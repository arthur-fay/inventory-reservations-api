"""Export or check the committed OpenAPI contract without connecting to a database."""

import argparse
import json
from pathlib import Path

from inventory_api.main import create_app

target = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
content = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
if args.check:
    if not target.exists() or target.read_text() != content:
        raise SystemExit("OpenAPI contract changed; run uv run python scripts/export_openapi.py")
    print("OpenAPI contract matches the application")
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    print("Updated docs/openapi.json")
