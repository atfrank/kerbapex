"""Generates a seamless instrumental loop with MusicGen. Runs inside LMU Director's Python (it has torch and
transformers), as its own process: `python music_worker.py '<json args>'`.

args: prompt, seconds (per clip, <= 30), clips, seed, model (folder or hub id), hf_home, out (wav), xfade (s).
Prints "DONE <i> <seconds>" after each clip, and "OUT <path>" at the end. Several clips are cross-faded into
one piece, and its end is blended into its start so the result repeats without a click.
"""
from __future__ import annotations

import json
import os
import sys
import time


def xfade(a, b, n):
    import numpy as np
    n = min(n, len(a), len(b))
    ramp = np.linspace(0.0, np.pi / 2, n, dtype=np.float32)           # equal-power
    mid = a[len(a) - n:] * np.cos(ramp) + b[:n] * np.sin(ramp)
    return np.concatenate([a[:len(a) - n], mid, b[n:]])


def make_loop(x, n):
    """Blend the last `n` samples into the first `n`, so playing the result on repeat has no seam."""
    import numpy as np
    n = min(n, len(x) // 3)
    ramp = np.linspace(0.0, np.pi / 2, n, dtype=np.float32)
    seam = x[len(x) - n:] * np.cos(ramp) + x[:n] * np.sin(ramp)
    return np.concatenate([x[n:len(x) - n], seam])


def main() -> int:
    a = json.loads(sys.argv[1])
    if a.get("hf_home"):
        os.environ["HF_HOME"] = a["hf_home"]
    import numpy as np
    import torch
    from scipy.io import wavfile
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    model_id = a["model"]
    proc = AutoProcessor.from_pretrained(model_id)
    model = MusicgenForConditionalGeneration.from_pretrained(model_id)
    model.eval()
    sr = model.config.audio_encoder.sampling_rate
    tokens = int(min(30.0, float(a["seconds"])) * model.config.audio_encoder.frame_rate)
    clips = []
    for i in range(int(a["clips"])):
        torch.manual_seed(int(a.get("seed", 1)) + i)
        t0 = time.time()
        inputs = proc(text=[a["prompt"]], padding=True, return_tensors="pt")
        with torch.no_grad():
            out = model.generate(**inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=tokens)
        clips.append(out[0, 0].float().numpy())
        print(f"\nDONE {i + 1} {time.time() - t0:.1f}", flush=True)
    n = int(float(a.get("xfade", 3.0)) * sr)
    x = clips[0]
    for c in clips[1:]:
        x = xfade(x, c, n)
    x = make_loop(x, n)
    peak = float(np.max(np.abs(x))) or 1.0
    wavfile.write(a["out"], sr, (x / peak * 0.9).astype(np.float32))
    print(f"\nOUT {a['out']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
