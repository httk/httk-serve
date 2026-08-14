"""Register httk-serve's durable, non-OPTIMADE publication family."""

from httk.core import register_entry_family, register_entry_record

register_entry_family(
    name="dsp-publications",
    family="httk.serve.dsp.config:DspPublicationEntry",
)
register_entry_record(
    name="serve-dsp-publication",
    record="httk.serve.dsp.config:DspDatasetPublication",
    family="dsp-publications",
)
