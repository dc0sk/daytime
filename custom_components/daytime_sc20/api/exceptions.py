"""Exceptions raised by the SC20 client."""

from __future__ import annotations


class SC20Error(Exception):
    """Base class for every error this package raises."""


class SC20ConnectionError(SC20Error):
    """The device could not be reached, or the connection dropped."""


class SC20Timeout(SC20Error):
    """A request went out but the matching response never arrived.

    The device never acknowledges writes, so this is only ever raised for reads.
    """


class SC20ProtocolError(SC20Error):
    """A frame arrived that does not fit the recovered protocol.

    Raised when parsing fails, not when a frame is merely unrecognised: unknown titles are
    ignored, because the firmware has more of them than this integration models.
    """


class SC20ValidationError(SC20Error, ValueError):
    """A value was rejected before being sent.

    Writes are unacknowledged and there is no undo on the device, so anything malformed is
    caught here rather than discovered after it has overwritten a working configuration.
    """
