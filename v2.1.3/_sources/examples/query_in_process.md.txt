# Query a live store-native OPTIMADE application without binding a port.

The ASGI application discovers ``StructureEntry`` from ``SqlStore`` and runs
each filter, sort, count, and page bound against the database. Saving another
structure after application construction demonstrates that no provider
snapshot or in-memory serving dataset exists.

```{literalinclude} ../../examples/optimade/query_in_process.py
:language: python
:lines: 8-
```
