from pathlib import Path

from httk.serve.web import serve

ROOT = Path(__file__).parent
serve(ROOT / "src", port=8080)
