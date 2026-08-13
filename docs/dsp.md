# DSP provider connector

`httk.serve.dsp` provides a mountable, in-memory provider-side control plane for
DSP 2025-1. It advertises one or more datasets, each with an unconditional ODRL
`use` offer and `HttpData-PULL` distribution, plus a separately serialized
DCAT-AP catalogue.
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

## Declaring datasets

Each `DspDatasetPublication` combines the neutral `httk.core.Dataset` metadata
with DSP-specific offer, distribution, service, access URL, and data-address
settings. A short script can declare several publications directly:

```python
config = DspProviderConfig(
    connector_root_url="https://provider.example/dsp",
    service_id="https://provider.example/dsp/service",
    participant_id="https://provider.example/participants/provider",
    catalog_id="https://provider.example/catalogs/materials",
    catalog_title="Materials catalogue",
    catalog_description="Published example datasets.",
    datasets=(first_publication, second_publication),
)
app = create_dsp_app(DspProvider(config))
```

The older singular `dataset=`, `offer_id=`, and related arguments still work
for a one-dataset configuration. `InlineDspDatasetSource` is available when a
source object is more convenient than the `datasets` tuple.

For stored records, `DspEntryProviderDatasetSource` reads one entry type from
any `httk.core.EntryProvider` and applies a caller-supplied function to each
record. In particular, this accepts `httk.store.db.StoreEntryProvider`, so SQL
storage remains behind the neutral provider boundary:

```python
source = DspEntryProviderDatasetSource(
    store_entry_provider,
    "published_datasets",
    publication_from_record,
)
config = DspProviderConfig(..., dataset_source=source)
```

The source is read once when `DspProvider` is constructed. The provider then
serves an immutable in-memory snapshot; later store changes require constructing
a new provider. At least one publication is required. Dataset, offer, and
distribution identifiers must be unique, and every dataset in one DCAT
catalogue must declare the same publisher identifier and name. A data service
may be shared when its identifier and title are consistent.

## Tier 1: public OPTIMADE access

Tier 1 advertises a public OPTIMADE API as a second DCAT `DataService`; it does
not replace the DSP access service embedded in a distribution:

```python
optimade = DcatDataService(
    id="https://provider.example/services/optimade",
    title="Public materials OPTIMADE API",
    endpoint_url="https://provider.example/optimade/v1",
    conforms_to=(
        "https://schemas.optimade.org/defs/v1.3/standards/optimade",
    ),
    endpoint_description="https://www.optimade.org/specification/latest/",
)

config = DspProviderConfig(
    ...,
    datasets=(structures, calculations),
    dcat_data_services=(optimade,),
)
```

With `serves_dataset_ids=None`, the default, the service's `dcat:servesDataset`
links resolve to every dataset in the provider's startup snapshot. An explicit
tuple advertises only that subset and fails startup if any ID is absent. The
owned DCAT projection also emits the endpoint as `dcat:endpointURL`, each
standard as `dct:conformsTo`, and the optional description as
`dcat:endpointDescription`.

The DSP projection deliberately does not include these companion services.
Each distribution's `accessService` remains the DSP service whose endpoint is
the connector's `/2025-1` root. The public OPTIMADE service may be mounted at
`/optimade` in the same composed ASGI application or hosted elsewhere.

Tier 1 is discovery, not access control: clients can query the advertised
OPTIMADE endpoint directly without negotiating through DSP. Future tiers may
return protected OPTIMADE connection information after transfer negotiation;
token issuance and endpoint enforcement remain outside this tier.

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
termination. An agreement is a unique `urn:uuid:` policy tied to the selected
catalogue dataset and the two protocol PIDs. Only a finalized agreement can
start a transfer.

The transfer profile accepts only `HttpData-PULL`. On transfer start the
provider sends that dataset's configured DSP `DataAddress`, whose HTTPS endpoint
must equal its distribution access URL. The service manages the control plane only: it
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
pagination/filter evaluation, live catalogue refresh, persistence, push or
streaming transfers, a generic RDF framework, and data-plane serving or
protection.
