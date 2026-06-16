# Focus series auto-save, dated naming, and zoneplate_z default — design

Date: 2026-06-16
Status: approved (pending implementation)

## Goal

After a through-focus series completes, the stack is saved **automatically**
to a dated, sequential filename, with full per-frame metadata, without the user
having to pick a path. The manual SAVE button is retained for custom exports.
Also switch the MCS2 focus stage from `mask_z` to `zoneplate_z`.

## Decisions (from brainstorming)

- **Exact-case sweep**: keep the existing floor behaviour — the negative
  extreme is the nearest-*smaller* whole-step symmetric span (never overshoots
  the requested range). No motion-logic change.
- **Auto-save**: auto-save every completed series **and** keep the manual SAVE
  button. Show a confirmation message after the auto-save.
- **Location/naming**: `<captures>/focus/YYMMDD_N.tif`
  (`YYMMDD` = 2-digit year/month/day, local time; e.g. `260616_1.tif`).
- **Sequence scope**: `N` counts **focus series only** — first focus series of
  the day is always `_1`, independent of kinetic/snapshot saves.
- **Stage**: focus PV base changes `mask_z` → `zoneplate_z` (both real and sim).
  The sim already serves `MCS2SIM:zoneplate_z`.

## Architecture (Approach A — client-triggered, server owns naming)

Reuses the existing save path end-to-end. `_focus_loop` returns to IDLE on
completion (`worker.py:673`); `save_focus_stack` is a service-level instant
command (`service.py:118,126`) that reads `_focus_frames` directly, independent
of worker state — so an auto-fired save behaves exactly like today's manual one.

### 1. Exact-case metadata (`marana_server/worker.py`)

No change to `half_steps = floor((range/2)/step)`. Add to `_focus_meta`:

- `swept_range_um = 2 * half_steps * step_um` (actual travel)
- existing `range_um` retained as the requested value
- `z_neg_um` / `z_end_um` extremes for unambiguous record

### 2. Server auto-naming (`marana_server/service.py`)

- New helper `_next_focus_path() -> str`:
  - ensure `<captures>/focus/` exists
  - `date = datetime.now().strftime("%y%m%d")` (local)
  - scan `focus/` for names matching `^(\d{6})_(\d+)\.tif$` with the matching
    date; `N = max(existing) + 1`, else `1`
  - return relative path `focus/{date}_{N}.tif`
- `_cmd_save_focus_stack`: if `args` has no `"path"` key, use
  `_next_focus_path()`; otherwise use the supplied path (manual SAVE unchanged).
  `max+1` guarantees no overwrite of an existing file.

### 3. Client auto-trigger + message (`marana_client/__main__.py`)

- On `TOPIC_FOCUS_COMPLETE` with `frames_done > 0`, auto-send
  `save_focus_stack` with **no path**; on reply, append to the status log:
  `Auto-saved focus stack: focus/260616_1.tif (… bytes)` (note `(partial)`
  when `partial` is true). Fires once per series.
- The SAVE button / `KineticSaveDialog` manual flow is unchanged.

### 4. Stage default (`marana_client/ui/focus_panel.py`)

- `PV_BASE_REAL = "MCS2:zoneplate_z"`, `PV_BASE_SIM = "MCS2SIM:zoneplate_z"`.
- Update test PV strings `mask_z` → `zoneplate_z` for consistency.

## Edge cases

- Partial/cancelled series with ≥1 frame still auto-save (nothing lost); message
  marks it partial.
- Auto-save failure (disk full, permission): logged as an error; manual SAVE
  remains available; series result is not lost from the buffer.
- Back-to-back series do not collide — the scan sees the just-written file.
- Client disconnected at completion: auto-save will not fire (accepted
  trade-off of client-triggered); manual SAVE is the fallback.

## Testing (filesystem-only / mocked camera — no hardware)

- `_next_focus_path()`: empty `focus/` → `_1`; existing `260616_3.tif` → `_4`;
  ignores other dates and non-matching patterns.
- `_cmd_save_focus_stack` with no `path` writes under `focus/` with the dated
  name and returns it.
- Metadata of a saved focus stack contains `swept_range_um`.
- Update existing focus tests to `zoneplate_z` and the bidirectional contract
  (already done for move counts / extremes).

All focus tests patch `EpicsMover` with a `MagicMock`; no channel-access puts —
the real MCS2 stage is never moved.
