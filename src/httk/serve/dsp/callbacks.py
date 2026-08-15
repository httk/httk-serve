"""DSP-facing names for the hardened outbound webhook transport.

The implementation moved to :mod:`httk.serve.http.webhook`. These names are
retained as the DSP-facing spelling of the generic transport and resolve to the
same objects, so identity and :func:`isinstance` checks continue to hold.
"""

from httk.serve.http.webhook import (
    PinnedHttpsJsonPoster,
    WebhookSender,
    WebhookTransportError,
    _ResolvedAddress,  # noqa: F401  # re-exported for the DSP test seam.
    _validate_callback_url,  # noqa: F401  # re-exported for the DSP test seam.
    _WebhookTarget,
    join_url_path,
)

CallbackSender = WebhookSender
CallbackTransportError = WebhookTransportError
DefaultCallbackSender = PinnedHttpsJsonPoster
callback_url = join_url_path
_CallbackTarget = _WebhookTarget

__all__ = ["CallbackSender", "CallbackTransportError", "DefaultCallbackSender", "callback_url"]
