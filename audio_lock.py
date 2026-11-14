"""One process-wide lock for native PyAudio lifecycle operations.

PortAudio/WASAPI is not safe when two threads create/terminate independent
PyAudio instances at the same time.  The call capture engine and the short
voice-input recorder both use PyAudio, so all create/open/close/terminate
operations must be serialized through this lock.
"""
from __future__ import annotations

import threading

AUDIO_LOCK = threading.RLock()
