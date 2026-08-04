import asyncio
import logging
import warnings
from collections.abc import Iterator

import httpx
import pytest
from definition_fixtures import served_schema
from fake_backend import FakeStore
from httk.core import report
from materials_fixtures import materials_field_handlers, materials_schema

from httk.serve.optimade import BackendAdapter, EntrySource, OptimadeConfig, create_asgi_app
from httk.serve.optimade.engine import processing
from httk.serve.optimade.model.results import ResultRow
from httk.serve.optimade.runtime.asgi import create_app


class EmptyResults:
    more_data_available = False

    def count(self) -> int:
        return 0

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(())


@pytest.fixture(autouse=True)
def _restore_reporting_state() -> Iterator[None]:
    loggers = [logging.getLogger("httk"), logging.getLogger("py.warnings")]
    logger_state = [(logger, list(logger.handlers), logger.level, logger.propagate) for logger in loggers]
    warning_filters = list(warnings.filters)
    showwarning = warnings.showwarning
    collector = report._collecting_handler
    capture_scopes = report._capture_scopes
    capture_permanent = report._capture_permanent
    yield
    for logger, handlers, level, propagate in logger_state:
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
                if handler is not collector:
                    handler.close()
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    warnings.filters[:] = warning_filters
    logging.captureWarnings(False)
    warnings.showwarning = showwarning
    report._collecting_handler = collector
    report._capture_scopes = capture_scopes
    report._capture_permanent = capture_permanent


def _app(query_function) -> object:
    return create_app(
        query_function=query_function,
        config=OptimadeConfig(),
        schema=served_schema({"structures": ["id", "type"]}),
        baseurl="http://testserver/",
    )


def _get(app: object, path: str, params: dict[str, str] | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, params=params)

    return asyncio.run(request())


def test_create_app_does_not_install_reporting_handlers() -> None:
    httk_handlers = list(logging.getLogger("httk").handlers)
    warning_handlers = list(logging.getLogger("py.warnings").handlers)

    _app(lambda *args, **kwargs: EmptyResults())

    assert logging.getLogger("httk").handlers == httk_handlers
    assert logging.getLogger("py.warnings").handlers == warning_handlers


def test_request_reports_apply_general_and_optimade_context_levels() -> None:
    logger = logging.getLogger("httk.test")

    def query(*args: object, **kwargs: object) -> EmptyResults:
        logger.warning("general-warning")
        logger.info("optimade-note", extra={"context": "optimade"})
        logger.info("plain-note")
        return EmptyResults()

    app = _app(query)
    response = _get(app, "/structures")

    assert response.status_code == 200
    collected = response.json()["meta"]["warnings"]
    details = [warning["detail"] for warning in collected]
    assert "general-warning" in details
    assert "optimade-note" in details
    assert "plain-note" not in details
    assert all(warning["type"] == "warning" for warning in collected)
    assert report.active_collections() == ()


def test_request_report_title_is_preserved() -> None:
    def query(*args: object, **kwargs: object) -> EmptyResults:
        logging.getLogger("httk.test").warning("titled-warning", extra={"title": "My title", "context": "optimade"})
        return EmptyResults()

    warnings_in_meta = _get(_app(query), "/structures").json()["meta"]["warnings"]

    assert {"type": "warning", "title": "My title", "detail": "titled-warning"} in warnings_in_meta


def test_error_response_keeps_collected_warning() -> None:
    fail = False

    def query(*args: object, **kwargs: object) -> EmptyResults:
        if fail:
            logging.getLogger("httk.test").warning("before-error")
            raise Exception("broken backend")
        return EmptyResults()

    app = _app(query)
    fail = True
    response = _get(app, "/structures")

    assert response.status_code == 500
    assert any(warning["detail"] == "before-error" for warning in response.json()["meta"]["warnings"])


def test_warnings_are_rearmed_for_each_request() -> None:
    ready = False

    def query(*args: object, **kwargs: object) -> EmptyResults:
        if ready:
            warnings.warn("salvage", RuntimeWarning)
        return EmptyResults()

    app = _app(query)
    ready = True
    first = _get(app, "/structures").json()["meta"]["warnings"]
    second = _get(app, "/structures").json()["meta"]["warnings"]

    assert any("salvage" in warning["detail"] for warning in first)
    assert any("salvage" in warning["detail"] for warning in second)


def test_existing_validation_warning_precedes_collected_warnings() -> None:
    def query(*args: object, **kwargs: object) -> EmptyResults:
        logging.getLogger("httk.test").warning("collected-warning")
        return EmptyResults()

    response = _get(_app(query), "/structures", params={"response_fields": "id,_other_prop"})

    collected = response.json()["meta"]["warnings"]
    assert "_other_prop" in collected[0]["detail"]
    assert collected[1]["detail"] == "collected-warning"


def test_late_warning_is_added_after_reply_meta_was_built(monkeypatch: pytest.MonkeyPatch) -> None:
    original = processing.generate_entry_endpoint_reply

    def reply_with_late_warning(*args: object, **kwargs: object) -> dict[str, object]:
        response = original(*args, **kwargs)
        logging.getLogger("httk.test").warning("late-warning")
        return response

    monkeypatch.setattr(processing, "generate_entry_endpoint_reply", reply_with_late_warning)

    response = _get(_app(lambda *args, **kwargs: EmptyResults()), "/structures")

    assert any(warning["detail"] == "late-warning" for warning in response.json()["meta"]["warnings"])


def test_httk_data_unknown_provider_property_warning() -> None:
    class Row:
        sid = "demo-1"

    adapter = BackendAdapter(
        store=FakeStore(rows_by_target={"structure-table": [Row()]}),
        sources={
            "structures": (
                EntrySource(
                    target="structure-table",
                    fields={"id": lambda row: row.sid, "type": lambda row: "structures"},
                ),
            )
        },
        schema=materials_schema(),
        field_handlers=materials_field_handlers(),
    )

    response = _get(
        create_asgi_app(adapter, baseurl="http://testserver/"),
        "/structures",
        params={"filter": "_unknownprov_foo=3"},
    )

    assert response.status_code == 200
    assert any(
        "filter references unknown property '_unknownprov_foo'" in warning["detail"]
        for warning in response.json()["meta"]["warnings"]
    )
