# httk-optimade

*httk-optimade* is a [*httk v2*](https://github.com/httk/httk2) module providing tooling for serving an [OPTIMADE](https://www.optimade.org/) API on top of httk data stores.

The served API version is **OPTIMADE v1.3.0**. Optional parts of the specification that are
not implemented: the `files`, `trajectories`, and `references` entry types, the partial data
protocol, per-property metadata, and sorting. See `docs/how_it_works.md` for the
architecture and the backend/web seams.

The implementation was ported from the OPTIMADE server in httk v1 (which served OPTIMADE
v1.0.0) and then upgraded to v1.3.0. The legacy client-side `validation/` subpackage has
intentionally not been ported; use the official
[`optimade-validator`](https://github.com/Materials-Consortia/optimade-python-tools) tool
to check conformance of a running server.
