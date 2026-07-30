"""Walking a live OPTIMADE API in-process, without binding a port

Everything a client sees — the JSON:API envelope, `meta`, `links.next`, the
compound document produced by `include` — is produced by the ASGI application,
not by the query engine underneath it. So the honest way to see what a server
answers is to speak HTTP to it. What this example shows is that you do not need
a port, a background thread, or a `curl` in another terminal to do so:
`create_asgi_app` returns an ordinary ASGI application, and starlette's
`TestClient` drives it directly in this process. Requests are real requests
(routing, query-string validation, error handling, response rendering all run);
they simply never touch a socket.

That makes this the shape to reach for in tests, in notebooks, and while
developing a provider: the whole API surface is available synchronously, and a
failure is a normal Python traceback rather than a log line in a server that is
still running.

The data here comes from the ready-made providers *httk-data* registers for the
standard `files` and `calculations` entry types, so no provider class has to be
written to have something to query — see
[Serving entry providers](../optimade/serving_providers.md) for how to write your own,
and `examples/optimade/provider_server/` for a runnable one. Only `sortable=` is
declared: a property is sortable only if the server says so in
`/v1/info/<entry>`, and `adapter_from_providers` forwards that declaration to
the served schema.

The walk below is the OPTIMADE query surface in order of how you would discover
it:

* `/v1/info` — what this implementation serves at all: API version, endpoints,
  entry types per format.
* `/v1/files` — an entry listing. `response_fields` narrows `attributes` to the
  properties asked for (plus the ones the standard requires a response to
  carry), which is how you keep a response small.
* `?filter=size > 100000` — a numeric comparison, offered because the property
  is described as an integer.
* `?filter=files.id HAS "file-2"` — list membership (`HAS`), here over the ids
  related to each calculation.
* `?sort=-size` — descending sort on a declared-sortable property.
* `?page_limit=2` plus `links.next` — pagination as a client should do it: keep
  following the link the server hands back until it is `null`.
* `?include=files` — a compound document: the related `files` resources are
  returned alongside the calculations, in a top-level `included` array.

Run it with `python examples/optimade/query_in_process.py`.
"""

import json
from typing import Any

from httk.core import RelatedEntry
from httk.data.entry_providers import CalculationEntryProvider, FileEntryProvider
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_providers, create_asgi_app

FILES = {
    "file-1": {
        "name": "INCAR",
        "url": "https://example.org/files/calc-1/INCAR",
        "size": 512,
        "media_type": "text/plain",
        "last_modified": "2021-03-15T10:20:00Z",
    },
    "file-2": {
        "name": "OUTCAR",
        "url": "https://example.org/files/calc-1/OUTCAR",
        "size": 204800,
        "media_type": "text/plain",
        "last_modified": "2021-03-15T10:25:00Z",
    },
    "file-3": {
        "name": "trajectory.xyz",
        "url": "https://example.org/files/calc-2/trajectory.xyz",
        "size": 1048576,
        "media_type": "chemical/x-xyz",
        "last_modified": "2021-03-16T08:00:00Z",
    },
}

CALCULATIONS = {
    "calc-1": {"last_modified": "2021-03-15T10:30:00Z"},
    "calc-2": {"last_modified": "2021-03-16T08:30:00Z"},
}

# Which files each calculation consumed and produced. The provider serves these
# as the calculation's OPTIMADE relationships block.
CALCULATION_FILES = {
    "calc-1": [RelatedEntry("files", "file-1", role="input"), RelatedEntry("files", "file-2", role="output")],
    "calc-2": [RelatedEntry("files", "file-3", role="output")],
}


def make_client() -> TestClient:
    """An HTTP client bound straight to the OPTIMADE app — no socket, no server process."""
    adapter = adapter_from_providers(
        [FileEntryProvider(FILES), CalculationEntryProvider(CALCULATIONS, relationships=CALCULATION_FILES)],
        # Sortable is a promise the server publishes in /v1/info/files; only
        # declared properties may be used in ?sort=.
        sortable={"files": ["size", "name"]},
    )
    # baseurl is what the server uses to build absolute URLs (links.next below),
    # so it must match the host the client talks to.
    app = create_asgi_app(adapter, baseurl="http://localhost/")
    return TestClient(app, base_url="http://localhost")


def show(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    """GET one OPTIMADE URL and print the response, JSON:API envelope included."""
    response = client.get(url, params=params)
    query = "?" + "&".join(f"{key}={value}" for key, value in params.items()) if params else ""
    print("\n=== GET " + url + query + " -> " + str(response.status_code))
    payload: dict[str, Any] = response.json()
    print(json.dumps(payload, indent=1, sort_keys=True))
    return payload


def main() -> None:
    client = make_client()

    # 1. What does this implementation serve? Capability discovery comes first,
    #    because everything below is only valid if /v1/info advertises it.
    info = show(client, "/v1/info")
    print("\nserved entry types:", info["data"]["attributes"]["entry_types_by_format"]["json"])

    # 2. An entry listing. response_fields trims 'attributes' to what is asked
    #    for (plus the required ones, such as 'url' here); without it every
    #    served property is returned, nulls included.
    show(client, "/v1/files", response_fields="name,size,media_type")

    # 3. Filtering. 'size' is described as an integer, so numeric comparisons
    #    are offered for it; no handler code was written for this.
    show(client, "/v1/files", filter="size > 100000", response_fields="name,size")

    # 4. List membership. HAS tests a list-valued property; here the list is the
    #    set of file ids related to each calculation.
    show(client, "/v1/calculations", filter='files.id HAS "file-2"')

    # 5. Sorting. A leading '-' sorts descending. Sorting on a property that was
    #    not declared sortable is an error response, not a silently ignored one.
    show(client, "/v1/files", sort="-size", response_fields="name,size")

    # 6. Pagination. Never construct the next page's URL yourself: follow
    #    links.next until the server stops handing one back.
    print("\n--- following links.next ---")
    page_url = "/v1/files?page_limit=2&response_fields=name,size"
    page_number = 0
    while page_url is not None:
        page_number += 1
        page = show(client, page_url)
        print(f"page {page_number}: " + ", ".join(entry["id"] for entry in page["data"]))
        page_url = page["links"]["next"]

    # 7. A compound document. The related resources named in each calculation's
    #    relationships block are returned in full in the top-level 'included'.
    compound = show(client, "/v1/calculations", include="files")
    print("\nincluded resources:", [(entry["type"], entry["id"]) for entry in compound["included"]])


if __name__ == "__main__":
    main()
