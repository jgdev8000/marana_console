# Design: Bidirectional Focus Sweep (Negative‑first, No Initial Frame)

**Date:** 2026‑06‑15

## Goal
Implement a new focus‑through series that:
1. Moves the selected motor **negative half the user‑defined range** without capturing any images.
2. Captures the **first frame at the negative extreme**.
3. Sweeps **forward** through the full range, capturing a frame **after each step** (including the step that returns to the original start position).
4. Optionally returns the motor to the original start position **without capturing** an extra frame.
5. Saves the stack to the areaDetector file plugin (TIFF/HDF5) with per‑frame metadata: Z position (µm), exposure time (s), AOI, and camera temperature.
6. Auto‑increments filenames (`YYMMDD_<n>.tif`).

The UI should hide direction controls, force a negative‑first sweep, and display the correct number of stops and end‑Z.

---

## High‑level Architecture
- **Server side (`CameraWorker`):**
  - Compute `half_steps = int((range_um/2) // step_um)`.
  - `stop_count = 1 + 2 * half_steps` (total frames).
  - Move to the negative extreme **silently** (no frames). 
  - Capture first frame at the negative extreme.
  - Loop over `idx` from `1` to `stop_count‑1` moving forward by `step_mm` each iteration and capturing a frame after each move.
  - Preserve existing *return‑to‑start* logic (move back only, no capture).
  - Store `z_positions_um` list for metadata.
  - Build metadata via existing `build_metadata()` which already includes `z_positions_um`, exposure, AOI, sensor temperature.

- **Client UI (`FocusPanel`):
  - Hide the direction radio buttons; `direction()` always returns `-1`.
  - `current_params()` no longer includes a selectable direction.
  - `_refresh_derived()` computes `half_steps = int((range/2)//step)`, `stops = 1 + 2*half_steps`, `end_z = start_z + half_steps*step` (positive extreme).
  - Estimated time unchanged (step count unchanged).
  - UI now shows:
    ```
    Stops: <stops>    End Z: <end_z> µm
    ```
  - All other controls (range, step, exposure, settle, return‑to‑start) remain.

- **Live preview:**
  - Unchanged – monitor `MARANA:image1:ArrayData` via CA (or optionally PVA). Frames are dropped automatically when the network cannot keep up, matching the existing ZMQ behaviour.

- **Stack storage & read‑back:**
  - AreaDetector file plugin (`MARANA:HDF1:` or `MARANA:TIFF1:`) writes the stack locally on the IOC host.
  - Filenames follow `YYMMDD_<n>.tif` (or `.h5`).
  - The server’s `save_focus_stack` command reads the saved file and returns `path`, `bytes_written`, `frames_written`.
  - Remote GUI reads the file via a shared mount or copies it locally – exactly as with kinetic stacks today.

---

## Detailed Server Changes
1. **`CameraWorker._h_start_focus`**
   ```python
   step_abs_mm = params["step_um"] * 1e-3
   half_steps = int((params["range_um"] / 2) // params["step_um"])
   stop_count = 1 + 2 * half_steps
   z_end_mm = z_start_mm + half_steps * step_abs_mm
   ```
   (Return payload unchanged – still contains `z_start_um`, `z_end_um`, `stop_count`, `est_time_s`, limits.)

2. **`CameraWorker._focus_loop`**
   - Move silent negative half‑range:
     ```python
     for i in range(1, half_steps+1):
         mover.move(z_start_mm - i*step_abs_mm)
         mover.wait_done(...)
     ```
   - Capture first frame at negative extreme and store `frames[0]` and `z_positions_um[0]`.
   - Positive sweep with capture after each step:
     ```python
     total_frames = stop_count
     for idx in range(1, total_frames):
         target_mm = (z_start_mm - half_steps*step_abs_mm) + idx*step_abs_mm
         mover.move(target_mm)
         mover.wait_done(...)
         frame = self._camera.single_shot(...)
         frames[idx] = frame
         z_positions_um.append(target_mm*1e3)
         self._publish_focus_frame(idx, total_frames, target_mm*1e3, frame)
     ```
   - Keep existing optional return‑to‑start move (no capture).
   - `self._focus_meta` now includes the full `z_positions_um` list for metadata.

3. **Metadata (`_cmd_save_focus_stack`)** already picks up `self._worker._focus_meta`, which now contains the Z list.

---

## Detailed UI Changes (`FocusPanel`)
- Remove direction radio buttons (`self.dir_pos`, `self.dir_neg`) from layout and connections.
- Override `direction()` to always return `-1` (negative‑first).
- `current_params()` no longer includes a direction key (or forces `-1`).
- `_refresh_derived()`:
  ```python
  half_steps = int((self.range_spin.value() / 2) // self.step_spin.value())
  stops = 1 + 2 * half_steps
  if self._start_z_um is None:
      self.derived_label.setText(f"Stops: {stops}    End Z: --")
  else:
      end_z = self._start_z_um + half_steps * self.step_spin.value()
      self.derived_label.setText(f"Stops: {stops}    End Z: {end_z:+.3f} µm")
  ```
- Estimated‑time label unchanged (step count unchanged).
- UI now only shows the fields: Motor source, Range, Step, AOI, Exposure, Settle, Return‑to‑start, and the derived label/estimate.

---

## Impact Assessment
- **No change to existing kinetic flow** – kinetic remains unchanged.
- **Live preview** stays functional; no additional bandwidth impact.
- **User workflow** simplifies – the operator no longer chooses direction; the sweep is deterministic.
- **Metadata completeness** is preserved; per‑frame Z positions are now part of the saved stack.
- **Backward compatibility** – existing scripts that set a direction will still work because the server ignores the direction field for focus.

---

## Acceptance Criteria
1. With Start Z = 5 µm, Range = 3 µm, Step = 0.3 µm the UI reports **Stops: 11**, **End Z: +6.5 µm**.
2. After pressing **START** the server moves silently to 3.5 µm, captures the first frame, then steps forward capturing a frame after each 0.3 µm increment, finishing at 6.5 µm (total 11 frames).
3. If *Return to start* is checked, the motor moves back to 5 µm after the last frame, **without** creating an extra frame.
4. The saved file (`YYMMDD_<n>.tif`) contains per‑frame metadata entries for Z‑position, exposure, AOI, and sensor temperature.
5. Live preview continues to work remotely, dropping frames if the network is slower than the camera frame rate.

---

## Next Steps
- Apply the server code patches.
- Apply the UI patches.
- Run unit‑test on `CameraWorker._focus_loop` to verify frame count and Z list.
- Manual integration test on the lab hardware (real camera + IOC) to confirm motion and saved metadata.
- Update documentation (README) to mention the new deterministic bidirectional focus sweep.

---

**Please review the design** above. Let me know if any part needs adjustment before we write the implementation plan and create the actual code changes.