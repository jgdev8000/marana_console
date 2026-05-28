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
