"""Thin wrapper around pyAndorSDK3 for the Marana-X.

Not thread-safe. Only the camera worker thread on the server should call into this.
"""
from __future__ import annotations

import logging
from typing import Optional

from marana_proto.errors import CameraDisconnected

log = logging.getLogger(__name__)

# Note: pyAndorSDK3 < 1.23 crashed in Camera.__init__ (__populate_config) on the
# SimCam because is_readable("MetadataEnable") returned AT_ERR_NOTIMPLEMENTED, and
# the wait_buffer/Acquisition path then KeyError'd on config["MetadataEnable"].
# Both are fixed in wrapper >= 1.24 (the installed version is 1.30.2), so the
# former monkey-patch has been removed. See ReleaseNotes for SDK 3.15.300114+.


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

    # --- acquisition primitives ------------------------------------------

    def _configure_single_frame_mode(self, exposure_s: float | None = None) -> None:
        cam = self._require()
        if exposure_s is not None:
            cam.ExposureTime = exposure_s
        cam.CycleMode = "Fixed"
        cam.FrameCount = 1
        cam.TriggerMode = "Internal"

    def _decode_buffer(self, raw_buf, image_bytes: int):
        """Decode a raw uint8 buffer into a (H, W) uint16 numpy array, trimming row stride."""
        import numpy as np
        cam = self._require()
        height = int(cam.AOIHeight)
        width = int(cam.AOIWidth)
        stride = int(cam.AOIStride)
        if not isinstance(raw_buf, np.ndarray):
            arr = np.frombuffer(raw_buf, dtype=np.uint8, count=image_bytes)
        else:
            arr = raw_buf[:image_bytes].view(np.uint8)
        view16 = arr[: height * stride].view(np.uint16)
        cols_in_stride = stride // 2
        view16 = view16.reshape(height, cols_in_stride)
        return view16[:, :width].copy()  # copy: caller can re-queue the buffer

    def single_shot(self, timeout_ms: int = 3000, exposure_s: float | None = None):
        import numpy as np
        from marana_proto.errors import AcquisitionTimeout
        cam = self._require()
        try:
            self._configure_single_frame_mode(exposure_s=exposure_s)
            image_bytes = int(cam.ImageSizeBytes)
            buf = np.empty(image_bytes, dtype=np.uint8)
            cam.queue(buf, image_bytes)
            cam.AcquisitionStart()
            try:
                cam.wait_buffer(timeout=timeout_ms)
            finally:
                cam.AcquisitionStop()
                cam.flush()
            return self._decode_buffer(buf, image_bytes)
        except Exception as e:
            if "TIMEDOUT" in str(e).upper() or "TIMEOUT" in str(e).upper():
                raise AcquisitionTimeout(str(e)) from e
            raise self._translate_sdk_error(e, "single_shot") from e

    def safe_continuous_iter(self, exposure_s: float | None = None, inter_frame_sleep_s: float = 0.01):
        """Generator that yields frames forever. Caller calls .close() or breaks to stop.

        Implements the BL11.3.2 ICE server's "safe continuous" pattern:
        per-frame CycleMode=Fixed + FrameCount=1 + AcquisitionStart/Stop cycle.
        """
        import numpy as np
        import time
        from marana_proto.errors import AcquisitionTimeout
        cam = self._require()
        self._configure_single_frame_mode(exposure_s=exposure_s)
        image_bytes = int(cam.ImageSizeBytes)
        try:
            while True:
                buf = np.empty(image_bytes, dtype=np.uint8)
                cam.queue(buf, image_bytes)
                cam.AcquisitionStart()
                try:
                    cam.wait_buffer(timeout=int(2000 + 1000 * (exposure_s or 0.05)))
                finally:
                    cam.AcquisitionStop()
                    cam.flush()
                yield self._decode_buffer(buf, image_bytes)
                if inter_frame_sleep_s > 0:
                    time.sleep(inter_frame_sleep_s)
        except GeneratorExit:
            return
        except Exception as e:
            if "TIMEDOUT" in str(e).upper() or "TIMEOUT" in str(e).upper():
                raise AcquisitionTimeout(str(e)) from e
            raise self._translate_sdk_error(e, "safe_continuous_iter") from e

    def kinetic_burst(
        self,
        frame_count: int,
        exposure_s: float,
        frame_rate_hz: float,
        on_progress=None,
        stop_flag=None,
    ):
        """Run a fixed-length burst into RAM as one continuous acquisition.
        Returns (frames_3d, frames_written, elapsed_s).

        Frames are buffered in RAM (frame_count x H x W uint16), so the caller is
        responsible for the memory budget. There is no inter-batch pausing — the
        old 25-frame/5 s batching workaround was removed once usbfs_memory_mb was
        raised and the SDK USB fixes landed (see README 'USB buffer memory')."""
        import numpy as np
        import time
        import threading
        from marana_proto.errors import KineticValidationError, AcquisitionTimeout

        if not (1 <= frame_count <= 10000):
            raise KineticValidationError(f"frame_count {frame_count} not in [1, 10000]")
        if not (0.0 < exposure_s <= 60.0):
            raise KineticValidationError(f"exposure_s {exposure_s} not in (0, 60]")
        if not (0.0 < frame_rate_hz <= 200.0):
            raise KineticValidationError(f"frame_rate_hz {frame_rate_hz} not in (0, 200]")
        if stop_flag is None:
            stop_flag = threading.Event()

        cam = self._require()
        cam.CycleMode = "Continuous"
        cam.TriggerMode = "Internal"
        cam.ExposureTime = exposure_s
        # Clamp the requested rate into the camera's currently-allowed range
        # (which depends on exposure + AOI). Setting a value above the max is
        # rejected (AT_ERR_OUTOFRANGE) and would silently leave FrameRate at a
        # stale low value, throttling the whole burst.
        try:
            lib, h = cam._lib, cam._handle
            try:
                fr_min = float(lib.get_float_min(h, "FrameRate"))
                fr_max = float(lib.get_float_max(h, "FrameRate"))
                target = max(fr_min, min(frame_rate_hz, fr_max))
            except Exception:
                target = frame_rate_hz
            cam.FrameRate = target
        except Exception:
            pass  # sim or small-AOI may reject FrameRate entirely; carry on

        image_bytes = int(cam.ImageSizeBytes)
        height = int(cam.AOIHeight)
        width = int(cam.AOIWidth)
        frames = np.zeros((frame_count, height, width), dtype=np.uint16)

        ring_size = min(16, frame_count)
        ring = [np.empty(image_bytes, dtype=np.uint8) for _ in range(ring_size)]

        t0 = time.monotonic()
        done = 0
        try:
            for r in range(ring_size):
                cam.queue(ring[r], image_bytes)
            cam.AcquisitionStart()
            try:
                for i in range(frame_count):
                    if stop_flag.is_set():
                        break
                    try:
                        cam.wait_buffer(timeout=int(2000 + 1000 * exposure_s))
                    except Exception as e:
                        if "TIMEDOUT" in str(e).upper() or "TIMEOUT" in str(e).upper():
                            raise AcquisitionTimeout(str(e)) from e
                        raise
                    buf = ring[i % ring_size]
                    frames[done] = self._decode_buffer(buf, image_bytes)
                    done += 1
                    next_q = i + ring_size
                    if next_q < frame_count:
                        cam.queue(ring[i % ring_size], image_bytes)
                    if on_progress is not None:
                        elapsed = time.monotonic() - t0
                        fps = done / elapsed if elapsed > 0 else 0.0
                        on_progress(done, frame_count, fps)
            finally:
                cam.AcquisitionStop()
                cam.flush()
        finally:
            try:
                cam.CycleMode = "Fixed"
            except Exception:
                pass

        return frames, done, time.monotonic() - t0

    # --- cooling ---------------------------------------------------------

    def get_cooling(self) -> dict:
        """Best-effort cooling readout. Returns defaults for fields the camera doesn't expose."""
        cam = self._require()
        out = {"enabled": False, "target_c": 0.0, "sensor_temp_c": 0.0, "status": "Unknown"}
        try:
            out["enabled"] = bool(cam.SensorCooling)
        except Exception:
            pass
        try:
            out["target_c"] = float(cam.TargetSensorTemperature)
        except Exception:
            pass
        try:
            out["sensor_temp_c"] = float(cam.SensorTemperature)
        except Exception:
            pass
        try:
            out["status"] = str(cam.TemperatureStatus)
        except Exception:
            pass
        return out

    def set_cooling(self, enable: bool, target_c: float | None = None) -> None:
        cam = self._require()
        if target_c is not None:
            try:
                cam.TargetSensorTemperature = float(target_c)
            except Exception:
                pass  # camera may not allow setting target while cooling off
        try:
            cam.SensorCooling = bool(enable)
        except Exception:
            pass

    # --- acquisition settings (speed / gain / encoding cluster) -----------

    def available_enum_options(self, name: str) -> list[str]:
        """Enum option strings whose index is currently available, per the SDK's
        is_enumerated_index_available. Raises if the feature isn't an enum / absent."""
        cam = self._require()
        lib = cam._lib
        h = cam._handle
        count = lib.get_enumerated_count(h, name)
        out: list[str] = []
        for i in range(count):
            try:
                if lib.is_enumerated_index_available(h, name, i):
                    out.append(lib.get_enumerated_string_by_index(h, name, i))
            except Exception:
                continue
        return out

    def get_acq_settings(self) -> dict:
        """Snapshot of the speed/gain/encoding cluster plus read-only indicators.
        Every field degrades to [] / None when the camera lacks the feature (SimCam)."""
        def avail(name):
            try:
                return self.available_enum_options(name)
            except Exception:
                return []

        def val(name):
            try:
                return self.get_feature(name)
            except Exception:
                return None

        def fmax(name):
            try:
                cam = self._require()
                return float(cam._lib.get_float_max(cam._handle, name))
            except Exception:
                return None

        keys = ("PixelReadoutRate", "PixelEncoding", "GainMode")
        return {
            "options": {k: avail(k) for k in keys},
            "values": {k: val(k) for k in keys},
            "readonly": {
                "bit_depth": val("BitDepth"),
                "readout_time_s": val("ReadoutTime"),
                "frame_rate_hz": val("FrameRate"),
                "max_frame_rate_hz": fmax("FrameRate"),
            },
        }
