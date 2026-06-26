#!/usr/bin/env python3
"""
mic_level.py - live microphone level meter for the Raspberry Pi voice client.

Captures with sounddevice exactly like voice_client.py (same device resolution,
same 16 kHz / 80 ms chunks, same int16 RMS unit), so it measures the very
microphone that works in normal operation - no arecord involved.

Two modes:
  (default)   view only: show the live mic level until Ctrl-C. Use it to see how
              loud the room and your voice are relative to VAD_SILENCE_THRESHOLD.
  --pick      type a threshold while watching the live level; on Enter the chosen
              number is printed to stdout (start.sh captures it). The live meter
              is drawn to /dev/tty so stdout stays clean for that value.

The running client holds the mic exclusively, so stop it first:
    sudo systemctl stop voice-client
(start.sh does this automatically when it calls this script.)
"""
import os
import re
import select
import sys
import termios
import tty
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK = 1280  # 80 ms - same chunk the client's VAD uses

ALSA_INPUT_DEVICE = os.getenv("ALSA_INPUT_DEVICE") or os.getenv("AUDIO_INPUT_DEVICE", "plughw:1,0")
THRESHOLD = int(float(os.getenv("VAD_SILENCE_THRESHOLD", "500")))


def resolve_input(alsa_dev: str):
    """Map an ALSA 'plughw:CARD,DEV' string to a sounddevice index (like the client)."""
    m = re.search(r":(\d+),", alsa_dev)
    if not m:
        return None
    card = m.group(1)
    for i, dev in enumerate(sd.query_devices()):
        if f"hw:{card}," in dev.get("name", "") and dev.get("max_input_channels", 0) > 0:
            return i
    return None


def render(rms: int, peak: int, typed: str, pick: bool, width: int = 40, per: int = 50) -> str:
    """One status line: a bar for the current level with a '|' marker at THRESHOLD."""
    fill = min(width, rms // per)
    mark = min(width - 1, THRESHOLD // per)
    cells = []
    for i in range(width):
        if i == mark:
            cells.append("|")            # where the threshold sits
        elif i < fill:
            cells.append("#")
        else:
            cells.append(" ")
    line = f"\r\033[2K  RMS {rms:5d}  peak {peak:5d}  thr {THRESHOLD:5d}  [{''.join(cells)}]"
    if pick:
        line += f"  threshold: {typed}_"
    return line


def main() -> None:
    pick = "--pick" in sys.argv[1:]
    idx = resolve_input(ALSA_INPUT_DEVICE)

    if pick:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        old = termios.tcgetattr(tty_fd)
        tty.setcbreak(tty_fd)            # read keys one at a time, no line buffering

        def draw(s: str) -> None:
            os.write(tty_fd, s.encode())
    else:
        tty_fd = None
        print("  Live mic level - press Ctrl-C to stop.")

        def draw(s: str) -> None:
            sys.stdout.write(s)
            sys.stdout.flush()

    typed = ""
    peak = 0
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=CHUNK, device=idx) as stream:
            while True:
                chunk, _ = stream.read(CHUNK)
                x = chunk[:, 0].astype(np.float32)
                rms = int((x ** 2).mean() ** 0.5) if x.size else 0
                peak = max(peak, rms)
                draw(render(rms, peak, typed, pick))
                if pick:
                    while select.select([tty_fd], [], [], 0)[0]:
                        ch = os.read(tty_fd, 1)
                        if ch in (b"\r", b"\n"):
                            raise SystemExit
                        if ch in (b"\x7f", b"\x08"):     # backspace
                            typed = typed[:-1]
                        elif ch.isdigit():
                            typed += ch.decode()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        msg = (f"\n  Could not read the microphone ({ALSA_INPUT_DEVICE}): {e}\n"
               f"  Is the device free? Stop the client: sudo systemctl stop voice-client\n")
        if pick:
            os.write(tty_fd, msg.encode())
        else:
            sys.stderr.write(msg)
    finally:
        if pick:
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old)
            os.write(tty_fd, b"\n")
            os.close(tty_fd)
            if typed:
                sys.stdout.write(typed)   # captured by start.sh
                sys.stdout.flush()
        else:
            sys.stdout.write("\n")


if __name__ == "__main__":
    main()
