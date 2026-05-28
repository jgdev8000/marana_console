# Andor Marana-X Camera Console

A camera server on the Linux box (USB3-attached Marana-X) + a PyQt6 GUI client that runs anywhere on the lab network.

- **Server** (`marana_server`): owns the camera, runs acquisition loops, speaks ZeroMQ.
- **Client** (`marana_client`): PyQt6 GUI for live preview, snapshots, kinetic bursts.
- **Wire protocol**: ZeroMQ REQ/REP for commands (port 5555) + PUB/SUB for frames (port 5556), msgpack-encoded.

Three acquisition modes: **Live** (continuous preview), **Snapshot** (single-frame save to PC), **Kinetic** (fixed-length burst saved on Linux box).

See `docs/superpowers/specs/2026-05-27-andor-marana-ui-design.md` for the full design.

## Quick start (single host, simulator)

```bash
git clone <this repo> ~/marana_console
cd ~/marana_console
python -m venv .venv && source .venv/bin/activate
pip install -e .[server,client,dev]
# Install the Andor SDK (see "Install Andor SDK" below). Required for sim work too.

# Terminal 1
python -m marana_server --sim --bind 127.0.0.1 --captures-dir /tmp/marana_caps

# Terminal 2
python -m marana_client --host 127.0.0.1
```

## Install Andor SDK (Linux box)

1. Extract the SDK tarball:
   ```bash
   tar -xzf andor-sdk3-3.15.30092.2.tar.gz
   cd andor
   ```
2. Run the installer as root:
   ```bash
   sudo ./install_andor
   ```
3. Install udev rules so the `andor` user can access the camera over USB3:
   ```bash
   sudo cp etc/99-andor-cameras.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules
   sudo udevadm trigger
   ```
4. Install the Python wrapper:
   ```bash
   pip install -e Python/pyAndorSDK3
   ```
5. Verify with the sim camera:
   ```bash
   python -c "import pyAndorSDK3; sdk = pyAndorSDK3.AndorSDK3(); print('devices:', sdk.DeviceCount)"
   ```

## Install Qt system dependencies (any client machine)

The PyQt6 wheel needs a few system libraries on Linux:

```bash
sudo apt-get install -y libgl1 libxkbcommon-x11-0 libdbus-1-3 libegl1 \
  libfontconfig1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0 \
  libxcb-xkb1 libxkbcommon0
```

(`qt6-base-dev` pulls them all if you prefer a single package.)

## Install Marana Console as a system service

```bash
git clone <this repo> /opt/marana/marana_console
sudo useradd -r -G plugdev,dialout -d /opt/marana andor || true
sudo chown -R andor:andor /opt/marana
sudo -u andor python -m venv /opt/marana/venv
sudo -u andor /opt/marana/venv/bin/pip install -e /opt/marana/marana_console[server]
sudo -u andor /opt/marana/venv/bin/pip install -e /path/to/andor/Python/pyAndorSDK3
sudo mkdir -p /var/lib/marana/captures /var/log/marana
sudo chown -R andor:andor /var/lib/marana /var/log/marana
```

## Run the server under systemd

```bash
sudo cp /opt/marana/marana_console/deploy/marana-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now marana-server
sudo journalctl -u marana-server -f
```

Check it's healthy:
```bash
python - <<'PY'
import zmq, msgpack
s = zmq.Context().socket(zmq.REQ); s.connect("tcp://localhost:5555")
s.send(msgpack.packb({"v": 1, "id": "x", "cmd": "hello", "args": {}}))
print(msgpack.unpackb(s.recv()))
PY
```

## Install the client (any machine)

```bash
git clone <this repo>
cd marana-console
python -m venv .venv && source .venv/bin/activate
pip install -e .[client]
python -m marana_client --host linuxbox.als.lbl.gov
```

## Configuration

Client config persists in `~/.marana_console/config.json`: host, ports, last save directories, contrast, window geometry.

CLI flags override the config; the chosen host is written back so subsequent launches default to it.

## Real-camera manual checklist

After installing on the actual hardware (real Marana plugged into USB3), run through this once:

1. **Server status**: `sudo systemctl status marana-server` should be active. `journalctl -u marana-server` should show `Opened real camera ... model='MARANA-...'`.
2. **Client connects**: launch the client. Status bar should be green; camera model should match the physical camera.
3. **Live preview**: switch to Live tab, click LIVE. Image should update within 1 second; FPS readout should be non-zero.
4. **Settings**: change Encoding to `Mono16` — should apply without losing preview. Change Speed — same.
5. **AOI**: set L/T/W/H to `256/256/512/512`, click SET. Live preview should reconfigure (brief blank) and show a smaller image. Click FULL — back to full sensor.
6. **Cooling**: enable cooling, set target -45 °C, click APPLY. Temperature readout should move toward target over ~minutes.
7. **Snapshot**: SNAP NOW → save dialog → save as `manual_snap.tif`. Open in ImageJ: should be uint16, correct dimensions.
8. **Acquire & Save**: click ACQUIRE & SAVE… (stops live, takes fresh exposure, saves). Verify file.
9. **Kinetic small**: Kinetic tab → 200 frames, 0.005s, 100 fps → START. Progress should advance; complete in ~2s. Scrub through frames. SAVE STACK… → server-side dialog → save. ssh to the Linux box and verify the file landed in `/var/lib/marana/captures/`.
10. **Kinetic large** (optional): 1000 frames at 256×256. Should auto-batch (look for batching messages in the server log).
11. **Reconnect**: stop the server (`sudo systemctl stop marana-server`); client should go amber within 5s. Start the server; SUB should reconnect and you can issue commands again (REQ may need a re-attempt after timeout).

## Project layout

See `docs/superpowers/plans/2026-05-28-andor-marana-ui.md` for the full file tree and implementation history.

## Troubleshooting

- **`No Andor devices found`** — udev rules not loaded, or user not in `plugdev`. Check `lsusb` for the camera, `ls -la /dev/bus/usb/...`, and that the `andor` user is in `plugdev`.
- **Client says DEGRADED but live frames keep flowing** — REQ is timing out but SUB is fine. Server is busy with a long acquisition; retry or increase the affected command's timeout.
- **Kinetic save fails with "path escapes captures dir"** — the path you typed included `..` or symlinks pointing out of the captures root. Pick a path under the listed directory.
- **`pyAndorSDK3` import fails** — SDK libraries not on the linker path. The installer copies them to `/usr/local/lib`; run `sudo ldconfig` after installation.
- **PyQt6 import fails with `libGL.so.1: cannot open shared object file`** — install the system Qt dependencies listed above.
- **SimCam quirks** — the SDK simulator has a fixed 2560×2160 sensor, no writable AOI, and doesn't support Mono16 encoding. These limitations don't apply to the real Marana.
