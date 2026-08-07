"""Development-server entry point for an OPTIMADE ASGI application."""

import uvicorn
from starlette.applications import Starlette


def run_dev_server(*, app: Starlette, host: str, port: int) -> None:
    """Run an ASGI application with Uvicorn.

    :param app: ASGI application to serve.
    :param host: Interface or hostname to bind.
    :param port: TCP port to bind.
    """

    uvicorn.run(app, host=host, port=port)
