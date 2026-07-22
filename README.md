# httk-optimade

*httk-optimade* is a [*httk₂*](https://github.com/httk/httk2) module providing tooling for serving an [OPTIMADE](https://www.optimade.org/) API on top of *httk₂* data stores.

The served API version is **OPTIMADE v1.3.0**. Implemented optional parts of the
specification include sorting, the `references`, `files`, and `trajectories` entry types,
relationships with the `include` query parameter, per-property metadata, the partial data
protocol (JSON Lines format and `dimension_slices`) with the compact list representation,
and the `license`/`available_licenses`/warnings meta and base-info fields. Optional parts
that remain unimplemented: cross-source sort merging, filtering on relationship
`.target.*`/`.description`/`.role` properties, the sparse JSON Lines layout, index
meta-databases, and rejection of unrecognized query parameters. See `docs/how_it_works.md`
for the architecture and the backend/web seams.

The implementation was ported from the OPTIMADE server in httk v1 (which served OPTIMADE
v1.0.0) and then upgraded to v1.3.0. The legacy client-side `validation/` subpackage has
intentionally not been ported; use the official
[`optimade-validator`](https://github.com/Materials-Consortia/optimade-python-tools) tool
to check conformance of a running server.
