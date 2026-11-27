"""V t565u: workshop noise suppression before STT (services/speech/denoise.py), synthetic audio."""
import importlib.util
import io
import time
import wave
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("denoise", Path(__file__).resolve().parents[1] / "services/speech/denoise.py")
dn = importlib.util.module_from_spec(spec); spec.loader.exec_module(dn)
SR = dn.SR


def voice_like(sec=6.0):
    """Harmonic «voice» (150 Hz + formant-ish harmonics) in 0.4 s bursts with pauses; mask of where it sounds."""
    t = np.arange(int(sec * SR)) / SR
    v = sum(np.sin(2 * np.pi * 150 * h * t) / h for h in range(1, 12)) * 0.15
    mask = (np.floor(t / 0.4) % 2 == 0)
    return (v * mask).astype(np.float32), mask


def shop_noise(n, seed=0):
    r = np.random.default_rng(seed)
    t = np.arange(n) / SR
    return (r.normal(0, 0.05, n) + 0.08 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)  # compressor hiss + mains hum


def db(x):
    return 10 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-12)


def test_noise_in_pauses_drops_speech_kept():
    v, mask = voice_like()
    x = v + shop_noise(len(v))
    t0 = time.time()
    y, st = dn.clean(x)
    assert st["denoise"] and time.time() - t0 < 1.0  # light, CPU-cheap
    scale = np.sqrt(np.mean(y[mask] ** 2) / np.mean(x[mask] ** 2))
    y = y / scale  # compare at the same speech level
    pause = ~mask
    assert db(x[pause]) - db(y[pause]) >= 8  # noise between words: at least 8 dB lower
    snr_in = db(v[mask]) - db(x[pause])
    snr_out = db(y[mask]) - db(y[pause])
    assert snr_out - snr_in >= 8


def test_hum_removed_and_quiet_voice_normalised():
    v, _ = voice_like(3.0)
    y, _ = dn.clean(0.05 * v + 0.2 * np.sin(2 * np.pi * 50 * np.arange(len(v)) / SR).astype(np.float32))
    f = np.fft.rfftfreq(len(y), 1 / SR); s = np.abs(np.fft.rfft(y))
    assert s[(f > 45) & (f < 55)].max() < 0.05 * s[(f > 145) & (f < 155)].max()
    assert np.max(np.abs(y)) >= 4 * 0.05 * np.max(np.abs(v)) and np.max(np.abs(y)) <= 0.91  # quiet voice raised (max +18 dB), no clipping


def test_short_or_silent_input_untouched():
    z = np.zeros(SR, np.float32)
    assert dn.clean(z)[1]["denoise"] is False and dn.clean(z[:100])[1]["denoise"] is False


def test_wav_with_noise_mixed_in_decodes_and_cleans():
    v, _ = voice_like(2.0)
    mix = np.clip(v + shop_noise(len(v), 1), -1, 1)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)  # phone rate -> resampled to 16 kHz
        up = np.interp(np.linspace(0, len(mix) - 1, len(mix) * 3), np.arange(len(mix)), mix)
        w.writeframes((up * 32767).astype("<i2").tobytes())
    x = dn.decode(b.getvalue())
    assert abs(len(x) - len(mix)) <= 2
    y, st = dn.clean(x)
    assert st["denoise"] and len(y) == len(x)
