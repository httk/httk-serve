"""Run a development ASGI server for a site application."""

import uvicorn

from httk.serve.http import ServeApp


def run_dev_server(*, app: ServeApp, host: str, port: int) -> None:
    """Run an application with the configured development server.

    :param app: ASGI application to serve.
    :param host: Interface on which to listen.
    :param port: TCP port on which to listen.
    """
    uvicorn.run(app, host=host, port=port)
