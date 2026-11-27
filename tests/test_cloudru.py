"""Cloud.ru provider: primary for RU-region users, TokenRouter fallback; STT chain; public health has no provider names."""
from types import SimpleNamespace

from digital_mechanic.agent.llm import REQUEST_COUNTRY, Llm
from digital_mechanic.config import Settings
from digital_mechanic import voice


def _s(**kw):
    return Settings(llm_api_key="tr", cloudru_api_key="cr", **kw)


def test_order_by_country():
    assert Llm(_s()).order("RU") == ["tokenrouter", "cloudru"]  # default after the bake-off
    l = Llm(_s(llm_primary="auto"))
    assert l.order("RU") == ["cloudru", "tokenrouter"] and l.order("UZ")[0] == "cloudru"
    assert l.order("IN") == ["tokenrouter", "cloudru"] and l.order("VN")[0] == "tokenrouter"
    tok = REQUEST_COUNTRY.set("RU"); assert l.order()[0] == "cloudru"; REQUEST_COUNTRY.reset(tok)
    assert Llm(Settings(llm_api_key="tr", cloudru_api_key="")).order("RU") == ["tokenrouter"]
    assert Llm(_s(llm_primary="tokenrouter")).order("RU")[0] == "tokenrouter"


def test_fallback_and_reasoning_stripped(monkeypatch):
    l = Llm(_s(llm_stream=False, llm_hedge_s=0, llm_primary="cloudru"))
    calls = []

    def create(kw, prov):
        calls.append((prov, kw["model"], "extra_body" in kw))
        if prov == "cloudru":
            raise ConnectionError("down")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="<think>x</think>Ответ", tool_calls=None))], usage=None)
    monkeypatch.setattr(l, "_create", create); monkeypatch.setattr("time.sleep", lambda *_: None)
    msg, _ = l.chat("t", [{"role": "user", "content": "q"}])
    assert msg.content == "Ответ"
    assert [c[0] for c in calls] == ["cloudru", "tokenrouter"]  # one attempt, then the other provider (V t564u: latency cap)
    assert calls[0][1] == l.s.cloudru_model and not calls[0][2] and calls[-1][2]  # thinking switch only for TokenRouter


def test_stt_chain(monkeypatch):
    seen = []
    monkeypatch.setattr(voice, "cloudru_stt", lambda *a: seen.append("cloudru") or "текст")
    monkeypatch.setattr(voice, "speech_stt", lambda *a: seen.append("local") or "local")
    s = _s(speech_url="http://x")
    assert voice.transcribe(b"a", "audio/wav", "ru", s, local_first=True, country="RU") == ("текст", "cloudru")
    seen.clear()
    assert voice.transcribe(b"a", "audio/wav", "uz", s, local_first=True, country="UZ")[1] == "whisper-local"  # UZ law: stand first
    seen.clear(); voice.transcribe(b"a", "audio/wav", "uz", s, local_first=False, country="RU"); assert seen == ["local"]  # uz never via Cloud.ru
    assert voice.transcribe(b"a", "audio/wav", "kk", s, local_first=True, country="KZ")[1] == "whisper-local"
    assert voice.transcribe(b"a", "audio/wav", "hi", s, local_first=False, country="IN")[1] == "whisper-local"  # not RU region
