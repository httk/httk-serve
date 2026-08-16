"""Query a live store-native OPTIMADE application without binding a port.

The ASGI application discovers ``StructureEntry`` from ``SqlStore`` and runs
each filter, sort, count, and page bound against the database. Saving another
structure after application construction demonstrates that no provider
snapshot or in-memory serving dataset exists.
"""

import json
from typing import Any

from httk.atomistic import Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.store.db import Database, SqlStore
from starlette.testclient import TestClient

from httk.serve.optimade import create_asgi_app


def structure(symbol: str, size: int) -> UnitcellStructure:
    """Build one simple cubic elemental structure."""
    return UnitcellStructure(
        [[size, 0, 0], [0, size, 0], [0, 0, size]],
        [[0, 0, 0]],
        [Species(symbol, (symbol,), (1,))],
        [symbol],
        chemical_formula_descriptive=symbol,
    )


def show(client: TestClient, url: str, **params: Any) -> dict[str, Any]:
    """GET and print one OPTIMADE response."""
    response = client.get(url, params=params)
    payload: dict[str, Any] = response.json()
    print(f"\n=== GET {url} -> {response.status_code}")
    print(json.dumps(payload, indent=1, sort_keys=True))
    return payload


def main() -> None:
    store = SqlStore(
        Database.sqlite(),
        entry_records={StructureEntry: UnitcellStructureRecord},
    )
    store.save(structure("Si", 4))
    store.save(structure("Ge", 5))

    # Passing the store directly is the production data path. TestClient only
    # replaces the network socket; the OPTIMADE request stack is otherwise real.
    app = create_asgi_app(store, baseurl="http://localhost/")
    with TestClient(app, base_url="http://localhost") as client:
        show(client, "/v1/info")
        show(
            client,
            "/v1/structures",
            filter='elements HAS "Si"',
            response_fields="elements,chemical_formula_descriptive",
        )
        first = show(client, "/v1/structures", sort="chemical_formula_descriptive", page_limit=1)
        if first["links"]["next"] is not None:
            show(client, first["links"]["next"])

        store.save(structure("C", 3))
        live = show(client, "/v1/structures", response_fields="elements")
        meta = live["meta"]
        print(
            "\nentries visible after a post-construction save:",
            meta["data_returned"],
            "(unfiltered endpoint total:",
            f"{meta['data_available']})",
        )


if __name__ == "__main__":
    main()
