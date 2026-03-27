from pathlib import Path

from httk.web import publish

ROOT = Path(__file__).parent
publish(ROOT / "src", ROOT / "public", "http://127.0.0.1/")
