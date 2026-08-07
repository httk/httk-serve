"""Run a development ASGI server for a site application."""

import uvicorn
from starlette.applications import Starlette


def run_dev_server(*, app: Starlette, host: str, port: int) -> None:
    """Run an application with the configured development server.

    :param app: ASGI application to serve.
    :param host: Interface on which to listen.
    :param port: TCP port on which to listen.
    """
    uvicorn.run(app, host=host, port=port)
