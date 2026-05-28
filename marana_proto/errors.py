class MaranaError(Exception):
    pass


class CameraDisconnected(MaranaError):
    pass


class FeatureNotWritable(MaranaError):
    pass


class AcquisitionTimeout(MaranaError):
    pass


class FeatureValueOutOfRange(MaranaError):
    pass


class KineticValidationError(MaranaError):
    pass


class RamBudgetExceeded(MaranaError):
    pass


_WIRE_NAME_TO_CLASS = {
    cls.__name__: cls
    for cls in (
        MaranaError,
        CameraDisconnected,
        FeatureNotWritable,
        AcquisitionTimeout,
        FeatureValueOutOfRange,
        KineticValidationError,
        RamBudgetExceeded,
    )
}


def to_wire(exc: MaranaError) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}


def from_wire(envelope: dict) -> MaranaError:
    name = envelope.get("type", "MaranaError")
    msg = envelope.get("message", "")
    cls = _WIRE_NAME_TO_CLASS.get(name)
    if cls is None:
        return MaranaError(f"{name}: {msg}")
    return cls(msg)
