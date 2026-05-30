"""Per-series wrapper around a single EPICS motor record.

Not thread-safe — the focus loop owns one EpicsMover for its lifetime.
The .stop() method is safe to call from a different thread (touches only
the .STOP PV via pyepics, which is internally thread-safe), so the dispatch
thread can interrupt a wait_done blocking in the focus thread.
"""
from __future__ import annotations

import logging
import threading
import time

from marana_proto.errors import CameraDisconnected, AcquisitionTimeout

log = logging.getLogger(__name__)


class EpicsMover:
    def __init__(self, pv_base: str, connect_timeout_s: float = 3.0):
        import epics
        self._pv_base = pv_base
        self._dmov_event = threading.Event()
        self._val = epics.PV(f"{pv_base}.VAL", auto_monitor=False)
        self._rbv = epics.PV(f"{pv_base}.RBV", auto_monitor=True)
        self._dmov = epics.PV(f"{pv_base}.DMOV", auto_monitor=True, callback=self._dmov_cb)
        self._stop = epics.PV(f"{pv_base}.STOP", auto_monitor=False)
        self._dhlm = epics.PV(f"{pv_base}.DHLM", auto_monitor=False)
        self._dllm = epics.PV(f"{pv_base}.DLLM", auto_monitor=False)
        self._egu = epics.PV(f"{pv_base}.EGU", auto_monitor=False)
        for pv in (self._val, self._rbv, self._dmov, self._stop,
                   self._dhlm, self._dllm, self._egu):
            if not pv.wait_for_connection(timeout=connect_timeout_s):
                self.close()
                raise CameraDisconnected(f"EPICS PV {pv.pvname} did not connect")
        if self._dmov.value == 1:
            self._dmov_event.set()

    def _dmov_cb(self, value=None, **kw):
        if value == 1:
            self._dmov_event.set()
        else:
            self._dmov_event.clear()

    def read_rbv_mm(self) -> float:
        v = self._rbv.get()
        if v is None:
            raise CameraDisconnected(f"{self._pv_base}.RBV returned None")
        return float(v)

    def read_limits_mm(self) -> tuple[float, float]:
        lo = self._dllm.get()
        hi = self._dhlm.get()
        if lo is None or hi is None:
            raise CameraDisconnected(f"{self._pv_base} limits returned None")
        return float(lo), float(hi)

    def egu(self) -> str:
        return str(self._egu.get() or "")

    def move(self, target_mm: float) -> None:
        self._dmov_event.clear()
        ok = self._val.put(target_mm, wait=False)
        if ok is None:
            raise CameraDisconnected(f"{self._pv_base}.VAL put failed")

    def wait_done(self, timeout_s: float, settle_s: float) -> None:
        if not self._dmov_event.wait(timeout=timeout_s):
            raise AcquisitionTimeout(
                f"{self._pv_base} DMOV did not go true within {timeout_s} s"
            )
        if settle_s > 0:
            time.sleep(settle_s)

    def stop(self) -> None:
        try:
            self._stop.put(1, wait=False)
        except Exception as e:
            log.warning("stop() failed: %s", e)

    def close(self) -> None:
        for attr in ("_val", "_rbv", "_dmov", "_stop", "_dhlm", "_dllm", "_egu"):
            pv = getattr(self, attr, None)
            if pv is not None:
                try:
                    pv.disconnect()
                except Exception:
                    pass
