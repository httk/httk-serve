from .base import Renderer, RenderResult
from .html import HtmlRenderer
from .httkweb_compat import HttkwebCompatRenderer
from .markdown import MarkdownRenderer
from .rst import RstRenderer

RENDERERS_BY_SUFFIX: dict[str, Renderer] = {
    ".md": MarkdownRenderer(),
    ".rst": RstRenderer(),
    ".html": HtmlRenderer(),
    ".httkweb": HttkwebCompatRenderer(),
}

__all__ = [
    "Renderer",
    "RenderResult",
    "MarkdownRenderer",
    "RstRenderer",
    "HtmlRenderer",
    "HttkwebCompatRenderer",
    "RENDERERS_BY_SUFFIX",
]
