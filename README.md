# Andor Marana-X Camera Console

Server + PyQt6 GUI client for the Andor Marana-X over the Andor SDK3.

See `docs/superpowers/specs/2026-05-27-andor-marana-ui-design.md` for the design.

## Install

### Server (Linux box, USB3-attached camera)
1. Extract `andor-sdk3-3.15.30092.2.tar.gz`, run `andor/install_andor` as root.
2. Install udev rules from `andor/etc/99-andor-cameras.rules`.
3. `python -m venv .venv && source .venv/bin/activate`
4. `pip install -e .[server]`
5. `pip install -e <path-to-extracted>/andor/Python/pyAndorSDK3`

### Client (any machine with Python 3.10+)
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e .[client]`

## Run (simulator, no hardware)
Terminal 1: `python -m marana_server --sim`
Terminal 2: `python -m marana_client --host localhost`
