import pytest
from marana_proto.errors import (
    MaranaError,
    CameraDisconnected,
    FeatureNotWritable,
    AcquisitionTimeout,
    FeatureValueOutOfRange,
    KineticValidationError,
    RamBudgetExceeded,
    to_wire,
    from_wire,
)


def test_to_wire_round_trip():
    exc = FeatureNotWritable("ExposureTime not writable during acquisition")
    envelope = to_wire(exc)
    assert envelope == {
        "type": "FeatureNotWritable",
        "message": "ExposureTime not writable during acquisition",
    }


def test_from_wire_round_trip():
    envelope = {"type": "AcquisitionTimeout", "message": "Wait buffer timed out"}
    exc = from_wire(envelope)
    assert isinstance(exc, AcquisitionTimeout)
    assert str(exc) == "Wait buffer timed out"
    assert isinstance(exc, MaranaError)


def test_unknown_type_falls_back_to_base():
    envelope = {"type": "SomethingNew", "message": "boom"}
    exc = from_wire(envelope)
    assert isinstance(exc, MaranaError)
    assert "SomethingNew" in str(exc)
    assert "boom" in str(exc)


def test_all_concrete_types_subclass_base():
    for cls in (
        CameraDisconnected,
        FeatureNotWritable,
        AcquisitionTimeout,
        FeatureValueOutOfRange,
        KineticValidationError,
        RamBudgetExceeded,
    ):
        assert issubclass(cls, MaranaError)


def test_bare_maranaerror_round_trips_cleanly():
    exc = MaranaError("base error message")
    envelope = to_wire(exc)
    assert envelope == {"type": "MaranaError", "message": "base error message"}
    decoded = from_wire(envelope)
    assert type(decoded) is MaranaError
    assert str(decoded) == "base error message"
