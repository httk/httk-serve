# httk-optimade

*httk-optimade* is a [*httk v2*](https://github.com/httk/httk2) module providing tooling for serving an [OPTIMADE](https://www.optimade.org/) API on top of httk data stores.

The implementation is ported from the OPTIMADE server in httk v1. The legacy client-side
`validation/` subpackage has intentionally not been ported; use the official
[`optimade-validator`](https://github.com/Materials-Consortia/optimade-python-tools) tool
to check conformance of a running server.
