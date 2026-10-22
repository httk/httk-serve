---
title: API
name: API
---
# OPTIMADE API

Everything on this site is read from an [OPTIMADE](https://www.optimade.org/) API that any
OPTIMADE client can use directly. Its base URL is this site's address followed by
`optimade/`, e.g. `https://example.org/optimade/`.

Example queries, relative to the base URL:

- `v1/info`: the served entry types and the service description.
- `v1/info/files`: the properties of one entry type, with their types and units.
- `v1/files?page_limit=10`: the first ten entries of a type.
- `v1/_httk_runs?filter=_httk_name="relax"`: entries matching an OPTIMADE filter.
- `v1/_httk_runs/ID?include=files`: one entry, together with the files it links to.

See the [OPTIMADE specification](https://github.com/Materials-Consortia/OPTIMADE/blob/master/optimade.rst)
for the filter grammar and the response format.
