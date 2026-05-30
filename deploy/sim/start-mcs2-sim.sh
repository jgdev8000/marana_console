#!/usr/bin/env bash
# Start a virtual MCS2 IOC exposing MCS2SIM:mask_{x,y,z} + zoneplate_{x,y,z}.
# Uses the existing motorSim binary.
set -e
SIM_ROOT=/home/cxrodev/opt/iocs/motorSimIOC
FIFO=/tmp/mcs2sim_ioc.fifo

# Create a FIFO so the IOC shell has a non-closing stdin and stays alive.
rm -f "$FIFO"
mkfifo "$FIFO"

cd "$SIM_ROOT/iocBoot/iocMotorSim"
tail -f "$FIFO" | exec "$SIM_ROOT/bin/linux-x86_64/motorSim" /home/cxrodev/marana_console/deploy/sim/st.cmd.mcs2sim
