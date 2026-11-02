"""Chatterbox speech worker that stays running. Started by radio.ChatterboxVoice with the Python of LMU Director's
Chatterbox runtime (this file imports only torch/chatterbox, nothing of StintRadio), it loads the model once and then
makes one WAV per request, so a sentence costs its generation time and not the ~20 s model load.

Requests: one JSON object per line on stdin {"id", "text", "out", "voice", "exaggeration"}.
Answers: a line that starts with @@ then JSON {"id", "ok", "secs", "error"}, after a first {"ready": true}. The model prints
progress text on the same stream, which is why the answers carry a marker."""
import json
import sys
import time


def reply(obj: dict) -> None:
    print("\n@@" + json.dumps(obj), flush=True)


def main(device: str, threads: int, cfg_weight: float, temperature: float) -> int:
    import torch
    import torchaudio
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        torch.set_num_threads(max(1, threads))
    from chatterbox.tts import ChatterboxTTS
    t0 = time.time()
    model = ChatterboxTTS.from_pretrained(device=device)
    reply({"ready": True, "secs": round(time.time() - t0, 1), "device": device})
    current = object()                               # the voice clip the model has embedded
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        job = json.loads(raw)
        t = time.time()
        try:
            kw = {}
            voice = job.get("voice") or None
            if voice != current:
                kw["audio_prompt_path"] = voice
                current = voice
            wav = model.generate(job["text"], exaggeration=job.get("exaggeration", 0.5), cfg_weight=cfg_weight,
                                 temperature=temperature, **kw)
            torchaudio.save(job["out"], wav.cpu(), model.sr)
            reply({"id": job["id"], "ok": True, "secs": round(time.time() - t, 1)})
        except Exception as e:                       # one bad sentence must not end the worker
            reply({"id": job.get("id"), "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(main(a[0] if a else "cpu", int(a[1]) if len(a) > 1 else 4, float(a[2]) if len(a) > 2 else 0.5,
                  float(a[3]) if len(a) > 3 else 0.8))
