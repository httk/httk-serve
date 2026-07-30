# Widgets

Widgets are small, static page components. Put a trusted site-local Python module
in `src/widgets/` and invoke it as a paragraph by itself:

```python
# src/widgets/hello.py
from httk.web.widgets import trusted_html


def render(context, *, name: str) -> str:
    return f"Hello, {name}!"
```

```md
{{ widget("site.hello", name="Ada") }}
```

The result is rendered after Markdown, reStructuredText, HTML, or compatibility
content conversion and before the site page template. Plain strings are HTML
escaped. Return `trusted_html("<strong>...</strong>")`, `Markup`, or
`WidgetRenderResult` only for reviewed HTML. Widget arguments are parsed as
literals; they are never evaluated as Python.

`httk.text` (also available as `text`) is a small built-in useful for examples.
Built-ins always use the `httk.` namespace; local widgets always use `site.` and
therefore cannot shadow them.

Widget invocations must occupy their complete source paragraph/block. Code
examples in Markdown fences or indented code, RST literal/doctest blocks, and
HTML `pre`, `script`, `style`, `textarea`, and `code` content remain literal.
Prefix an otherwise standalone invocation with a backslash to render it literally:

```md
\{{ widget("site.hello", name="Ada") }}
```

Use the umbrella command during authoring:

```console
httk web list src
httk web check src
httk web serve src --reload
```

`check` validates every content page, including widget parsing, names, local
module signatures, duplicate ids, and a static render. `list` prints canonical
widget names and their source locations. `serve --reload` uses uvicorn's
process-level reload supervisor.
