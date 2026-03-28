import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath("../src"))

project = "httk-web"
author = "The httk-web AUTHORS"
copyright = f"{date.today().year}, {author}"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build"]

autosummary_generate = True

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": False,
    "show-inheritance": True,
}

html_theme = "furo"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3
