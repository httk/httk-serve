from pathlib import Path

from httk.web import publish

ROOT = Path(__file__).parent
publish(ROOT / "src", ROOT / "public", "./", compatibility_mode=True)
