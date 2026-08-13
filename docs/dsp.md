# DSP provider connector

`httk.serve.dsp` provides a mountable, in-memory provider-side control plane for
DSP 2025-1. It advertises one dataset, one unconditional ODRL `use` offer, one
`HttpData-PULL` distribution, and a separately serialized DCAT-AP catalogue.
It composes independently with the web and OPTIMADE applications:

```python
app = compose_asgi_apps(
    [
        ASGIAppMount("/optimade", optimade_app),
        ASGIAppMount("/dsp", create_dsp_app(provider)),
    ],
    root=ASGIAppMount("/", web_app),
)
```

`DspProviderConfig.connector_root_url` is the externally visible HTTPS mount
root, such as `https://provider.example/dsp`. Version discovery is deliberately
unversioned at `/.well-known/dspace-version`; the provider endpoints are below
`/2025-1`. Public URLs come from this explicit configuration rather than
request headers.

## Catalogue representations

The DSP catalogue and local DCAT-AP catalogue are two projections of one
internal model. They are not claimed to be one RDF graph satisfying both
profiles:

- DSP endpoints use the official protected DSP context and advertise
  `Distribution.format` as `HttpData-PULL`, the selected transfer profile.
- `GET /2025-1/catalog` uses an owned inline context and advertises `dct:format`
  as the EU File Type NAL `JSON_LD` IRI. Its publisher, access URL, service, and
  endpoint URL values expand as RDF IRIs.

This split is intentional. A DSP transfer-profile token is not a file format,
and the protected official DSP `endpointURL` term expands a value as a literal
where DCAT-AP requires an IRI. The DCAT response media type is
`application/ld+json; profile="https://semiceu.github.io/DCAT-AP/releases/3.0.1/"`.

The implementation vendors the official DSP Draft 2019-09 schemas from release
commit `0a30bdd30c36ffb329e1c29fd819faf9f33e0f22`. It also vendors the DCAT-AP
3.0.1 context and validation artifacts, including the all-in-one SHACL pinned at
SHA-256 `990d3e42721de6a4be8cc338a7171559f195e62dea89c0b56531356b78cc026f`.
Runtime DSP validation is fully offline.

## Negotiation and transfer profile

The provider implements consumer-initiated contract negotiation through
`REQUESTED`, `OFFERED`, `ACCEPTED`, `AGREED`, `VERIFIED`, and `FINALIZED`, plus
termination. An agreement is a unique `urn:uuid:` policy tied to the configured
dataset and the two protocol PIDs. Only a finalized agreement can start a
transfer.

The transfer profile accepts only `HttpData-PULL`. On transfer start the
provider sends its configured DSP `DataAddress`, whose HTTPS endpoint must equal
the distribution access URL. The service manages the control plane only: it
does not implement the HTTP data endpoint, authorize it, issue access tokens,
or define token lifetime.

Automatic progression sends the agreement and finalization callbacks during
negotiation and the start callback during transfer. State advances only after a
2xx acknowledgement. A failed delivery keeps the last acknowledged DSP state,
records a local out-of-sync delivery status, and attempts a best-effort
termination. The default sender allows HTTPS only, disables redirects, bounds
timeouts/body size/concurrency/retries, and rejects private, loopback,
link-local, multicast, and reserved destinations. Deployments may inject a
sender with their own network policy.

All state is process-local and non-durable. Restarting the application loses
negotiations, agreements, transfers, and delivery status.

## Conformance boundary

The precise claim is: **provider-side DSP 2025-1 control-plane subset, with a
catalogue projection passing the pinned DCAT-AP 3.0.1 mandatory, range, and
controlled-vocabulary validation profile.** Recommended-property SHACL results
are informational. This is not a complete DSP TCK certification or a claim of
deployment-independent DCAT-AP conformance.

The connector intentionally excludes consumer-role callbacks, provider-started
initial negotiation, DID/DCP/OAuth/VC identity, generic ODRL evaluation,
multiple datasets, pagination/filter evaluation, persistence, push or streaming
transfers, a generic RDF framework, and data-plane serving or protection.
