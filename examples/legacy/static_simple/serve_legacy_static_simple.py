from pathlib import Path

from httk.web import serve

ROOT = Path(__file__).parent
serve(ROOT / "src", port=8080, compatibility_mode=True, config_name="config_dynamic")
