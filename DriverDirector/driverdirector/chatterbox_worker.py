"""Chatterbox TTS worker. Runs inside a Python 3.11 environment that has `chatterbox-tts` installed
(LMU Director's runtime venv works) and synthesizes a batch of lines, then exits so the ~3 GB model
is released before video work starts.

    python chatterbox_worker.py job.json

job.json: {"device": "cpu", "threads": 8, "cfg_weight": 0.5, "temperature": 0.8,
           "lines": [{"text": "...", "out": "x.wav", "voice": "<reference wav or null>", "exaggeration": 0.5}]}
Lines should be ordered so lines sharing a voice are adjacent: the reference clip is only re-embedded
(a few seconds) when it changes. Progress: `DONE <index> <seconds>` on stdout; errors on stderr.
"""
import json
import sys
import time


def main(job_path: str) -> int:
    job = json.load(open(job_path, encoding="utf-8"))
    import torch
    import torchaudio

    device = job.get("device", "cpu")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        torch.set_num_threads(max(1, job.get("threads", 8)))
    from chatterbox.tts import ChatterboxTTS

    t0 = time.time()
    model = ChatterboxTTS.from_pretrained(device=device)
    print(f"LOADED {time.time() - t0:.1f} device={device}", flush=True)
    current = object()                       # sentinel: no reference prepared yet
    for i, line in enumerate(job["lines"]):
        t = time.time()
        voice = line.get("voice") or None
        kw = {}
        if voice != current:                 # only (re)embed the reference clip when the voice changes
            kw["audio_prompt_path"] = voice
            current = voice
        wav = model.generate(line["text"], exaggeration=line.get("exaggeration", 0.5),
                             cfg_weight=job.get("cfg_weight", 0.5), temperature=job.get("temperature", 0.8), **kw)
        torchaudio.save(line["out"], wav.cpu(), model.sr)
        # leading "\n" keeps the marker on its own line even amid progress-bar text on the shared stream
        print(f"\nDONE {i} {time.time() - t:.1f} audio={wav.shape[-1] / model.sr:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
