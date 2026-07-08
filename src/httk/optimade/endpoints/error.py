from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError
from ..model.request import EndpointResponse, RawRequest
from ..model.versions import optimade_default_version
from .meta import generate_meta


def format_optimade_error(
    ex: Exception,
    request: RawRequest,
    config: OptimadeConfig,
    version: str = optimade_default_version,
) -> EndpointResponse:
    if isinstance(ex, OptimadeError):
        response_code = ex.response_code
        title = ex.response_msg
        detail = ex.content
    else:
        response_code = 500
        title = "Internal Server Error"
        detail = str(ex)

    response = {
        "errors": [
            {
                "status": response_code,
                "title": title,
                "detail": detail,
            }
        ],
        "meta": generate_meta(
            representation=request.representation,
            api_version=version,
            config=config,
            data_count=1,
            more_data_available=False,
        ),
    }

    return EndpointResponse(
        json_response=response,
        content_type='application/vnd.api+json',
        response_code=response_code,
        response_msg=title,
        encoding='utf-8',
    )
