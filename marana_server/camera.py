"""Thin wrapper around pyAndorSDK3 for the Marana-X.

Not thread-safe. Only the camera worker thread on the server should call into this.
"""
from __future__ import annotations

import logging
from typing import Optional

from marana_proto.errors import CameraDisconnected

log = logging.getLogger(__name__)


def _install_pyandorsdk3_sim_patch() -> None:
    """The SimCam returns AT_ERR_NOTIMPLEMENTED for is_readable(MetadataEnable),
    which crashes pyAndorSDK3.Camera.__init__. Patch __populate_config to tolerate it.
    No-op on cameras that do implement MetadataEnable (e.g. real Marana)."""
    try:
        from pyAndorSDK3.andor_camera import Camera
        from pyAndorSDK3.andor_sdk3_exceptions import ATCoreException
    except ImportError:
        return
    if getattr(Camera, "_marana_patched", False):
        return
    orig = Camera._Camera__populate_config

    def patched(self, force=False):
        try:
            orig(self, force)
        except ATCoreException as e:
            if getattr(e, "err_code", None) == 2:  # AT_ERR_NOTIMPLEMENTED
                return
            raise

    Camera._Camera__populate_config = patched
    Camera._marana_patched = True


_install_pyandorsdk3_sim_patch()


class MaranaCamera:
    def __init__(self) -> None:
        self._sdk = None
        self._cam = None  # pyAndorSDK3.andor_camera.Camera

    def open(self, sim: bool = False) -> None:
        if self._cam is not None:
            return  # idempotent
        import pyAndorSDK3
        self._sdk = pyAndorSDK3.AndorSDK3()
        count = self._sdk.DeviceCount
        if count == 0:
            raise CameraDisconnected("No Andor devices found")
        selected_index: Optional[int] = None
        for i in range(count):
            try:
                cam = self._sdk.GetCamera(i)
            except Exception as e:
                log.debug("skip device %d: %s", i, e)
                continue
            try:
                model = cam.CameraModel
            except Exception:
                cam.close()
                continue
            is_sim = "SIM" in model.upper()
            if sim == is_sim:
                self._cam = cam
                selected_index = i
                break
            cam.close()
        if self._cam is None:
            mode = "simulator" if sim else "real camera"
            raise CameraDisconnected(f"No {mode} found among {count} device(s)")
        log.info("Opened device %d: model=%r serial=%r", selected_index, self.model, self.serial)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception as e:
                log.warning("Error closing camera: %s", e)
            self._cam = None

    def _require(self):
        if self._cam is None:
            raise CameraDisconnected("Camera not open")
        return self._cam

    @property
    def model(self) -> str:
        return self._require().CameraModel

    @property
    def serial(self) -> str:
        return self._require().SerialNumber

    @property
    def sensor_width(self) -> int:
        return int(self._require().SensorWidth)

    @property
    def sensor_height(self) -> int:
        return int(self._require().SensorHeight)

    # --- feature access ---------------------------------------------------

    def _translate_sdk_error(self, exc: Exception, feature: str | None = None) -> Exception:
        from marana_proto.errors import FeatureNotWritable, FeatureValueOutOfRange
        import pyAndorSDK3
        msg = str(exc)
        if isinstance(exc, pyAndorSDK3.CameraException) or isinstance(exc, pyAndorSDK3.ATCoreException):
            text = msg.upper()
            if "NOTWRITABLE" in text or "NOT WRITABLE" in text or "READ ONLY" in text:
                return FeatureNotWritable(f"{feature}: {msg}" if feature else msg)
            if "OUTOFRANGE" in text or "OUT OF RANGE" in text or "INDEXNOTAVAILABLE" in text or "STRINGNOTAVAILABLE" in text:
                return FeatureValueOutOfRange(f"{feature}: {msg}" if feature else msg)
        return exc

    def get_feature(self, name: str):
        cam = self._require()
        try:
            return getattr(cam, name)
        except Exception as e:
            raise self._translate_sdk_error(e, name) from e

    def set_feature(self, name: str, value) -> None:
        cam = self._require()
        try:
            setattr(cam, name, value)
        except Exception as e:
            raise self._translate_sdk_error(e, name) from e

    def enum_options(self, name: str) -> list[str]:
        """List the available enum string options for a feature.
        Raises if the feature isn't an enum."""
        cam = self._require()
        try:
            # pyAndorSDK3's ATCore exposes get_enumerated_string_options
            return list(cam._lib.get_enumerated_string_options(cam._handle, name))
        except Exception as e:
            raise self._translate_sdk_error(e, name) from e

    # --- AOI --------------------------------------------------------------

    def set_aoi(self, x0: int, x1: int, y0: int, y1: int) -> None:
        from marana_proto.errors import FeatureValueOutOfRange
        if not (0 <= x0 <= x1 < self.sensor_width and 0 <= y0 <= y1 < self.sensor_height):
            raise FeatureValueOutOfRange(
                f"AOI ({x0},{x1},{y0},{y1}) out of sensor "
                f"({self.sensor_width}x{self.sensor_height})"
            )
        width = x1 - x0 + 1
        height = y1 - y0 + 1
        cam = self._require()
        try:
            cam.AOIWidth = width
            cam.AOIHeight = height
            cam.AOILeft = x0 + 1     # SDK is 1-based
            cam.AOITop = y0 + 1
        except Exception as e:
            raise self._translate_sdk_error(e, "AOI") from e

    def get_aoi(self) -> tuple[int, int, int, int]:
        cam = self._require()
        left = int(cam.AOILeft) - 1   # to 0-based
        top = int(cam.AOITop) - 1
        width = int(cam.AOIWidth)
        height = int(cam.AOIHeight)
        return (left, left + width - 1, top, top + height - 1)

    def set_aoi_full(self) -> None:
        self.set_aoi(0, self.sensor_width - 1, 0, self.sensor_height - 1)
