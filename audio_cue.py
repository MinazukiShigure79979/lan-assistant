"""Small, dependency-free recording cues for voice input.

The tones are generated once as in-memory WAV data and played asynchronously so
starting/stopping a recording never waits on the audio device.  All failures
are swallowed: a missing output device must never break dictation.
"""
from __future__ import annotations

import math
import struct
import threading

try:
    import winsound
except Exception:  # non-Windows / stripped runtime
    winsound = None  # type: ignore[assignment]

_SAMPLE_RATE = 16000

def _wav(frequency: float, milliseconds: int, volume: float) -> bytes:
    samples = max(1, int(_SAMPLE_RATE * milliseconds / 1000))
    fade = max(1, int(_SAMPLE_RATE * 0.006))
    frames = bytearray()
    for index in range(samples):
        envelope = min(1.0, index / fade, (samples - index) / fade)
        value = int(
            32767.0
            * volume
            * envelope
            * math.sin(2.0 * math.pi * frequency * index / _SAMPLE_RATE)
        )
        frames += struct.pack("<h", max(-32768, min(32767, value)))
    data_size = len(frames)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, _SAMPLE_RATE, _SAMPLE_RATE * 2, 2, 16)
    header += b"data" + struct.pack("<I", data_size)
    return header + bytes(frames)


# A short high "di" on start, a softer lower "du" on stop.
_START = _wav(920.0, 55, 0.085)
_STOP = _wav(560.0, 90, 0.075)


def _play(data: bytes) -> None:
    if winsound is None:
        return

    def run() -> None:
        try:
            # PlaySound does not support SND_MEMORY together with SND_ASYNC.
            # Play synchronously on a tiny daemon thread instead, so dictation
            # start/stop never waits for the 55–90 ms tone.
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except Exception:
            # No sound device, exclusive audio mode, or a stripped runtime: cues
            # are best-effort and must never affect recording.
            pass

    threading.Thread(target=run, name="voice-recording-cue", daemon=True).start()


def play_start() -> None:
    _play(_START)


def play_stop() -> None:
    _play(_STOP)
