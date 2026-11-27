"""Workshop audio clean-up before STT (V t565u): high-pass (hum, rumble) + light spectral gating (steady noise:
compressors, fans, idling engines) + peak normalisation. Pure numpy, ~20-40 ms per 10 s of audio on CPU.
Speech detection itself is Silero VAD inside faster-whisper (vad_filter, MIT), tuned in server.py.
The gate is light on purpose (floor -20 dB): Whisper copes with residual noise better than with musical artefacts."""
from __future__ import annotations

import io
import wave

import numpy as np

SR = 16000
N_FFT, HOP = 512, 128
HP_HZ = 90.0       # below: hum and rumble, no speech energy
FLOOR = 0.1        # max attenuation of a bin (-20 dB): light suppression
OVER = 1.5         # noise over-subtraction factor
NOISE_PCT = 15     # noise profile = this percentile of each bin over time (pauses between words)


def decode(data: bytes) -> np.ndarray:
    """Any container (webm/opus from the phone, wav, ogg) -> float32 mono 16 kHz in [-1, 1]."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() == 2:
                x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
                ch, sr = w.getnchannels(), w.getframerate()
                if ch > 1:
                    x = x.reshape(-1, ch).mean(axis=1)
                if sr != SR:
                    n = int(len(x) * SR / sr)
                    x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
                return x
    except (wave.Error, EOFError):
        pass
    import av  # faster-whisper's own decoder

    out = []
    with av.open(io.BytesIO(data)) as c:
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        for fr in c.decode(audio=0):
            for r in rs.resample(fr) or []:
                out.append(r.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def _frames(x: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(x) - N_FFT) // HOP)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def highpass(x: np.ndarray) -> np.ndarray:
    """Whole-signal FFT high-pass: 0 below HP_HZ, cosine ramp to 1.4*HP_HZ (no leakage of a strong hum into speech bins)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.clip((f - HP_HZ) / (0.4 * HP_HZ), 0, 1)
    return np.fft.irfft(X * (0.5 - 0.5 * np.cos(np.pi * g)), n=len(x)).astype(np.float32)


def clean(x: np.ndarray) -> tuple[np.ndarray, dict]:
    """Return (cleaned audio, stats). Short or silent input is returned as is."""
    if len(x) < N_FFT * 4 or not np.any(x):
        return x, {"denoise": False}
    x = highpass(x)
    pad = (-(len(x) - N_FFT)) % HOP
    xp = np.concatenate([np.zeros(N_FFT // 2, np.float32), x, np.zeros(N_FFT // 2 + pad, np.float32)])
    win = np.hanning(N_FFT).astype(np.float32)
    spec = np.fft.rfft(_frames(xp) * win, axis=1)
    mag = np.abs(spec) + 1e-9
    noise = np.percentile(mag, NOISE_PCT, axis=0)
    gain = np.clip((mag - OVER * noise[None, :]) / mag, FLOOR, 1.0)
    # smooth over time (3 frames) against musical noise
    k = np.ones(3, np.float32) / 3
    gain = np.apply_along_axis(lambda g: np.convolve(g, k, mode="same"), 0, gain)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    gain[:, freqs < HP_HZ] = 0.0
    y_fr = np.fft.irfft(spec * gain, n=N_FFT, axis=1) * win
    y = np.zeros(len(xp), np.float32)
    norm = np.zeros(len(xp), np.float32)
    for i, fr in enumerate(y_fr):
        s = i * HOP
        y[s:s + N_FFT] += fr
        norm[s:s + N_FFT] += win ** 2
    y = (y / np.maximum(norm, 1e-6))[N_FFT // 2:N_FFT // 2 + len(x)]
    peak = float(np.max(np.abs(y))) or 1.0
    y = (y * min(0.9 / peak, 8.0)).astype(np.float32)  # normalise a far/quiet voice, never more than +18 dB
    return y, {"denoise": True, "noise_floor_db": round(float(20 * np.log10(np.median(noise) + 1e-9)), 1)}
