import os
import warnings
from datetime import date

from sphinx.deprecation import RemovedInSphinx10Warning
warnings.filterwarnings("ignore", category=RemovedInSphinx10Warning)

project = "httk-web"
author = "The httk-web AUTHORS"
copyright = f"{date.today().year}, {author}"

extensions = [
    # Core API docs
    "sphinx.ext.autodoc",        # pull docstrings
    "sphinx.ext.autosummary",    # API summary tables + stub gen
    "sphinx.ext.napoleon",       # Google/NumPy docstrings
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.mathjax",        # math rendering via MathJax

    # Nice-to-haves
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",

    # Markdown + notebooks
    "myst_nb",                   # .ipynb support

    "autoapi.extension",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "**/.ipynb_checkpoints"]

# Autosummary: generate stub pages automatically
autosummary_generate = True

# Autodoc defaults (tweak to taste)
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": False,
    "show-inheritance": True,
}
autodoc_typehints = "signature"
typehints_fully_qualified = False
typehints_document_rtype = True
typehints_defaults = "comma"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_attr_annotations = True

# MyST / Markdown configuration (math + nice syntax)
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
    "dollarmath",  # enables $...$ and $$...$$
]
myst_heading_anchors = 3

# myst-nb config: don't execute notebooks during docs build by default
nb_execution_mode = "off"

html_theme = "furo"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

# Optional: helpful external linking (edit as needed)
# httk-web builds on public httk-core objects, so cross-project references
# resolve against the published httk documentation site. The base URL comes from
# the DOCS_BASE_URL Makefile variable (exported as HTTK_DOCS_BASE_URL); the
# default below keeps bare sphinx invocations working. Inventories are vendored
# in docs/_inventories/ so docs builds need no network access; link targets still
# point at the live sites. Refresh the committed inventories with
# `make docs-inventories`.
_docs_base_url = os.environ.get("HTTK_DOCS_BASE_URL", "https://docs.httk.org")

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", "_inventories/python.inv"),
    "httk-core": (f"{_docs_base_url}/httk-core/", "_inventories/httk-core.inv"),
    # Public Starlette types (e.g. the Starlette application returned by
    # create_asgi_app) resolve against Starlette's own vendored inventory.
    "starlette": ("https://www.starlette.io/", "_inventories/starlette.inv"),
}

autoapi_options = [
       "members",
       "undoc-members",
       "show-inheritance",
       "show-module-summary",
       "imported-members",
]
autoapi_root = "reference/autoapi"
autoapi_ignore = []  # include everything

autoapi_type = "python"
autoapi_dirs = ["../src/httk"]
autoapi_add_toctree_entry = True
autoapi_keep_files = True
autoapi_member_order = "bysource"
autoapi_python_class_content = "module"  # docstring under class, not merged from __init__
autoapi_python_use_implicit_namespaces = True
autoapi_template_dir = "_templates/autoapi"

nitpicky = True
nitpick_ignore = [
    ("py:class", "typing.Any"),
    ("py:class", "typing.Optional"),
    ("py:class", "typing.Union"),
    ("py:class", "Ellipsis"),
]
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

# The real cross-project references to httk-core objects are resolved structurally
# via the httk-core intersphinx inventory above. The remaining
# "autoapi.python_import_resolution" notice is only AutoAPI's static parser being
# unable to follow httk.core imports: httk.core lives in a separate distribution
# that shares the PEP 420 "httk" namespace, so it is not among the source trees
# AutoAPI parses here. There is no source-level remedy, so this specific subtype
# is suppressed while all reference checking stays strict.
suppress_warnings = ["myst.xref_missing", "autoapi.python_import_resolution"]

def skip_member(app, what, name, obj, skip, options):
    # Skip private members (those starting with _)
    if name.startswith('_'):
        return True
    return skip

def setup(sphinx):
    sphinx.connect('autoapi-skip-member', skip_member)
