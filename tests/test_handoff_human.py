"""Hand-off summary input: the check reads as a person writes it (the summary copied «2026-10-29», «0.76», «0.73»)."""
from digital_mechanic.agent.handoff import human_check
from digital_mechanic.agent.grounding import unverified_numbers
import json


def test_human_check_formats():
    ch = {"session_id": "S-0005", "status": "anomaly", "anomaly_score": 0.76, "confidence": 0.73, "checked_at": "2026-10-29T15:20:00+03:00"}
    h = human_check(ch)
    assert h["дата"] == "29 окт 2026" and h["оценка"] == "0,76" and h["уверенность"] == "73%"
    assert "anomaly_score" not in h and "confidence" not in h and "checked_at" not in h
    assert human_check(None) is None


def test_human_numbers_are_grounded():
    ch = {"anomaly_score": 0.76, "confidence": 0.73, "checked_at": "2026-10-29T15:20:00"}
    ev = [json.dumps(ch), json.dumps(human_check(ch), ensure_ascii=False)]
    assert unverified_numbers("Проверка 29 окт 2026, оценка 0,76, уверенность 73%.", ev) == []


def test_msk_time_is_moscow_whatever_the_server_zone():
    from digital_mechanic.history import msk_time
    assert msk_time(0) == "01.01.1970 03:00 МСК"
