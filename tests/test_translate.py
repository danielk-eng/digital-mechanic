from digital_mechanic.store import Store
from digital_mechanic.translate import Translator, looks_russian, numbers_kept


class FakeLlm:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def json(self, step, system, user, max_tokens=0):
        import json
        self.calls += 1
        return {"items": [self.fn(x) for x in json.loads(user)["items"]]}


def test_looks_russian():
    assert looks_russian("Проверить затяжку гаек", "uz")
    assert not looks_russian("МАЗ-5337 · ЯМЗ-236М2", "uz")  # model names alone
    assert not looks_russian("Тексериш тәртібі", "kk")  # already Kazakh
    assert not looks_russian("Dvigatel taqillayapti", "uz")


def test_numbers_and_codes_kept():
    assert numbers_kept("Момент 98,06…122,58 Н·м, болт М12, F08 [S1]", "Moment 98,06…122,58 N·m, M12 bolt, F08 [S1]")
    assert not numbers_kept("зазор 0,25 мм", "oraliq 0,35 mm")
    assert not numbers_kept("двигатель ЯМЗ-236М2", "dvigatel YaMZ-236M2")


def test_translate_cache_and_fallback(tmp_path):
    store = Store(str(tmp_path / "a.db"))
    llm = FakeLlm(lambda x: "tarjima 0,25 mm" if "0,25" in x else "noto'g'ri 1")
    tr = Translator(llm, store, str(tmp_path / "none"))
    r = tr.translate(["зазор 0,25 мм", "зазор клапанов"], "uz")
    assert r[0] == {"text": "tarjima 0,25 mm", "src": "llm"}
    assert r[1]["src"] == "failed" and r[1]["text"] == "зазор клапанов"  # a number appeared from nowhere -> original
    r2 = tr.translate(["зазор 0,25 мм", "Dvigatel"], "uz")
    assert r2[0]["src"] == "cache" and r2[1]["src"] == "same" and llm.calls == 1


def test_kg_by_local_stt_and_kyrgyz_has_no_whisper():
    from digital_mechanic import privacy_laws, voice
    assert privacy_laws.requires_localization("KG") and privacy_laws.requires_localization("BY")
    assert {"bn", "ky"} <= voice.DEEPGRAM_LANGS and "be" not in voice.DEEPGRAM_LANGS and "ky" in voice.NO_WHISPER


def test_nvidia_translation_first_and_llm_repairs_rejected_items(tmp_path):
    """NVIDIA is the first batch translator, but a translation that invents/loses numbers is retried by the normal LLM."""
    store = Store(str(tmp_path / "a.db"))
    nvidia = FakeLlm(lambda x: "nv 0,25 mm" if "0,25" in x else "bad 1")
    nvidia.available = True
    llm = FakeLlm(lambda x: "llm 0,25 mm" if "0,25" in x else "yaxshi")
    tr = Translator(llm, store, str(tmp_path / "none"))
    tr.nvidia = nvidia
    r = tr.translate(["зазор 0,25 мм", "зазор клапанов"], "uz")
    assert r[0] == {"text": "nv 0,25 mm", "src": "nvidia"}
    assert r[1] == {"text": "yaxshi", "src": "llm"}
    assert nvidia.calls == 1 and llm.calls == 1
