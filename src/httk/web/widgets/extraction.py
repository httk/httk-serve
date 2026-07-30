"""Renderer-aware extraction of literal-only ``widget(...)`` invocations."""

import ast
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from httk.web.model.errors import WidgetParseError, WidgetValidationError
from httk.web.renderers.base import WidgetPlacement

_NAME = re.compile(r"(?:(?:httk|site)\.)?[a-z][a-z0-9_-]*\Z")
_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_:-]*\Z")
_CLASS_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_MARKDOWN_RAW_CODE_TAG = re.compile(r"<\s*(/?)\s*(pre|code|script|style|textarea)\b[^>]*>", re.IGNORECASE)


def parse_widget_invocation(text: str, *, source_path: Path, line: int, column: int) -> tuple[str, dict[str, object]]:
    """Parse one invocation without evaluation or general Python expressions."""

    source = text.strip()
    if source.startswith("{{") and source.endswith("}}"):
        source = source[2:-2].strip()
    try:
        expression = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise _parse_error("expected a literal widget(...) call", text, source_path, line, column) from exc
    call = expression.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "widget":
        raise _parse_error("only widget(...) is allowed", text, source_path, line, column)
    if len(call.args) != 1 or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
        raise _parse_error("widget() needs exactly one literal string name", text, source_path, line, column)
    name = call.args[0].value
    if _NAME.fullmatch(name) is None:
        raise _parse_error("invalid widget name", text, source_path, line, column)
    props: dict[str, object] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise _parse_error("**kwargs are not allowed", text, source_path, line, column)
        if _KEY.fullmatch(keyword.arg) is None:
            raise _parse_error(f"invalid property name: {keyword.arg}", text, source_path, line, column)
        if keyword.arg in props:
            raise _parse_error(f"duplicate property: {keyword.arg}", text, source_path, line, column)
        props[keyword.arg] = _literal(keyword.value, text, source_path, line, column)
    _validate_props(props, text, source_path, line, column)
    return name, props


def _literal(node: ast.expr, text: str, source_path: Path, line: int, column: int) -> object:
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, bool | int | float | str)):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(item, text, source_path, line, column) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal(item, text, source_path, line, column) for item in node.elts)
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            raise _parse_error("dictionary unpacking is not allowed", text, source_path, line, column)
        result: dict[object, object] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                continue
            literal_key = _literal(key, text, source_path, line, column)
            try:
                hash(literal_key)
            except TypeError as exc:
                raise _parse_error(
                    "dictionary keys must be hashable literals", text, source_path, line, column
                ) from exc
            result[literal_key] = _literal(value, text, source_path, line, column)
        return result
    raise _parse_error(
        "properties may contain only literal values, lists, tuples, and dictionaries", text, source_path, line, column
    )


def _validate_props(props: dict[str, object], text: str, source_path: Path, line: int, column: int) -> None:
    widget_id = props.get("id")
    if widget_id is not None and (not isinstance(widget_id, str) or _ID.fullmatch(widget_id) is None):
        raise WidgetValidationError(
            "id must be a token beginning with a letter",
            source_path=source_path,
            line=line,
            column=column,
            snippet=text,
            correction='use id="summary" or omit id for a deterministic local id',
        )
    for key in ("class", "class_"):
        value = props.get(key)
        if value is not None and (
            not isinstance(value, str) or any(_CLASS_TOKEN.fullmatch(x) is None for x in value.split())
        ):
            raise WidgetValidationError(
                f"{key} must contain space-separated class-like tokens",
                source_path=source_path,
                line=line,
                column=column,
                snippet=text,
            )


def _parse_error(message: str, text: str, source_path: Path, line: int, column: int) -> WidgetParseError:
    return WidgetParseError(
        message,
        source_path=source_path,
        line=line,
        column=column,
        snippet=text,
        correction='use {{ widget("site.example", title="Literal") }} as a complete paragraph',
    )


def placement_for(text: str, *, source_path: Path, line: int, column: int, index: int) -> WidgetPlacement:
    name, props = parse_widget_invocation(text, source_path=source_path, line=line, column=column)
    digest = hashlib.sha256(f"{source_path}:{line}:{column}:{index}".encode()).hexdigest()[:16]
    return WidgetPlacement(
        placeholder=f"HTTK_WIDGET_{digest}",
        name=name,
        props=props,
        source_path=source_path,
        line=line,
        column=column,
        snippet=text.strip(),
    )


def markdown_source(source: str, source_path: Path, *, line_offset: int = 0) -> tuple[str, tuple[WidgetPlacement, ...]]:
    """Replace only standalone Markdown paragraphs, excluding code blocks."""

    lines = source.splitlines(keepends=True)
    placements: list[WidgetPlacement] = []
    fence: str | None = None
    raw_html_code_stack: list[str] = []
    index = 0
    position = 0
    while position < len(lines):
        line = lines[position]
        stripped = line.strip()
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            position += 1
            continue
        if fence is not None or line.startswith(("    ", "\t")):
            position += 1
            continue
        if raw_html_code_stack:
            _update_markdown_raw_code_stack(raw_html_code_stack, line)
            position += 1
            continue
        if _MARKDOWN_RAW_CODE_TAG.search(line):
            _update_markdown_raw_code_stack(raw_html_code_stack, line)
            position += 1
            continue
        if not stripped:
            position += 1
            continue
        start = position
        paragraph: list[str] = []
        while position < len(lines) and lines[position].strip() and not lines[position].startswith(("    ", "\t")):
            if re.match(r"^\s*(`{3,}|~{3,})", lines[position]):
                break
            paragraph.append(lines[position])
            position += 1
        candidate = "".join(paragraph).strip()
        escaped = candidate.startswith("\\{{ widget(") and candidate.endswith(" }}")
        active = candidate.startswith("{{ widget(") and candidate.endswith(" }}")
        if escaped or active:
            invocation = candidate[1:] if escaped else candidate
            if active:
                column = len(paragraph[0]) - len(paragraph[0].lstrip()) + 1
                placement = placement_for(
                    invocation,
                    source_path=source_path,
                    line=line_offset + start + 1,
                    column=column,
                    index=index,
                )
                index += 1
                lines[start:position] = [placement.placeholder + ("\n" if paragraph[-1].endswith("\n") else "")]
                position = start + 1
                placements.append(placement)
            else:
                lines[start:position] = [invocation + ("\n" if paragraph[-1].endswith("\n") else "")]
                position = start + 1
    return "".join(lines), tuple(placements)


def _update_markdown_raw_code_stack(stack: list[str], line: str) -> None:
    """Track raw HTML code-like containers without treating their text as Markdown."""

    for match in _MARKDOWN_RAW_CODE_TAG.finditer(line):
        closing, tag = match.group(1), match.group(2).lower()
        if closing:
            for index in range(len(stack) - 1, -1, -1):
                if stack[index] == tag:
                    del stack[index:]
                    break
        elif not match.group(0).rstrip().endswith("/>"):
            stack.append(tag)


class _HtmlInvocationCollector(HTMLParser):
    _SKIP: ClassVar[frozenset[str]] = frozenset({"pre", "script", "style", "textarea", "code"})

    def __init__(self, source: str, source_path: Path) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.source_path = source_path
        self.lines = source.splitlines(keepends=True)
        self.skip_depth = 0
        self.replacements: list[tuple[int, int, str]] = []
        self.placements: list[WidgetPlacement] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self._SKIP:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        candidate = data.strip()
        escaped = candidate.startswith("\\{{ widget(") and candidate.endswith(" }}")
        active = candidate.startswith("{{ widget(") and candidate.endswith(" }}")
        if not escaped and not active:
            return
        line, col = self.getpos()
        start = sum(len(item) for item in self.lines[: line - 1]) + col
        leading = len(data) - len(data.lstrip())
        replacement_start = start + leading
        invocation = candidate[1:] if escaped else candidate
        if escaped:
            self.replacements.append((replacement_start, replacement_start + len(candidate), invocation))
            return
        prefix = data[:leading]
        leading_lines = prefix.count("\n")
        placement = placement_for(
            invocation,
            source_path=self.source_path,
            line=line + leading_lines,
            column=(len(prefix.rsplit("\n", 1)[-1]) + 1) if leading_lines else col + leading + 1,
            index=len(self.placements),
        )
        self.placements.append(placement)
        self.replacements.append((replacement_start, replacement_start + len(candidate), placement.placeholder))


def html_source(source: str, source_path: Path) -> tuple[str, tuple[WidgetPlacement, ...]]:
    """Extract complete HTML text blocks while deliberately skipping code-like tags."""

    if "widget(" not in source:
        return source, ()
    collector = _HtmlInvocationCollector(source, source_path)
    collector.feed(source)
    collector.close()
    if not collector.replacements:
        return source, ()
    out = source
    for start, end, replacement in reversed(collector.replacements):
        out = out[:start] + replacement + out[end:]
    return out, tuple(collector.placements)
