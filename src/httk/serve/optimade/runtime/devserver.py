import uvicorn
from starlette.applications import Starlette


def run_dev_server(*, app: Starlette, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port)
