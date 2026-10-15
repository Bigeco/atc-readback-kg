import sys
import numpy as np
from faster_whisper import WhisperModel
audio = np.fromfile(sys.argv[1], dtype=np.int16).astype(np.float32) / 32768
m = WhisperModel("large-v3", device="cuda", compute_type="float16")
segs, info = m.transcribe(audio, language="en", vad_filter=True, beam_size=5,
    initial_prompt="Air traffic control radio. Callsigns like American 1234, Delta 567, November 12345. Runway two two left, climb and maintain, contact departure, cleared for takeoff, squawk, heading, flight level.")
for s in segs:
    print(f"[{s.start:7.2f}-{s.end:7.2f}] {s.text.strip()}")
