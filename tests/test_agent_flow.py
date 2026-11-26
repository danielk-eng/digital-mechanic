"""Agent flow with fake ML/TOIR/LLM: policy gates, grounding, confirm-before-ticket."""
from pathlib import Path

import pytest

from digital_mechanic.agent.llm import Llm, LlmUnavailable
from digital_mechanic.agent.orchestrator import MechanicAgent
from digital_mechanic.clients import Fleet
from digital_mechanic.config import Settings
from digital_mechanic.ingest.pages import ingest_manuals
from digital_mechanic.retrieval.bm25 import ManualIndex
from digital_mechanic.schemas import CheckResult, CheckStatus, RecordingQuality, TicketAck

ROOT = Path(__file__).resolve().parents[1]


class FakeMl:
    def __init__(self, check):
        self.check = check

    def list_checks(self, vid):
        return [{"session_id": self.check.session_id, "datetime": "2026-11-01T00:00:00"}]

    def get_check(self, vid, sid):
        return self.check


class FakeToir:
    def __init__(self):
        self.posted = []

    def create_ticket(self, d):
        self.posted.append(d)
        return TicketAck(ticket_id="TKT-0001")


class FakeLlm(Llm):
    def __init__(self, answers):
        super().__init__(Settings(llm_api_key="x"))
        self.answers = list(answers)

    def json(self, step, system, user, max_tokens=0, attempt=0):
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture(scope="module")
def index():
    pages = ingest_manuals(ROOT / "data/mock/manuals", ROOT / "data/engine_docs.yaml")
    return ManualIndex(pages)


def make(index, check, llm_answers):
    fleet = Fleet(ROOT / "data/mock/vehicles.json")
    toir = FakeToir()
    a = MechanicAgent(fleet, FakeMl(check), toir, index, FakeLlm(llm_answers))
    a.select_vehicle("s", check.vehicle_id)
    return a, toir


def anomaly(**kw):
    base = dict(vehicle_id="A123BC77", session_id="SES-0001", status=CheckStatus.anomaly, fault_code="F02", fault_title="Стуки клапанного механизма",
                anomaly_score=0.86, confidence=0.91, quality=RecordingQuality.good, symptoms="стук в головке блока")
    base.update(kw)
    return CheckResult(**base)


def page_quote(index, engine, doc_id, page, n=60):
    p = index.get_page(engine, doc_id, page)
    line = next(l.strip() for l in p.text.splitlines() if len(l.strip()) > n)
    return line[:n + 20]


def good_llm(index):
    q = page_quote(index, "ЯМЗ-236М2", "YAMZ-236M2-RM", 5)
    # pick the page where the quote lives via search to stay robust
    hit = index.search("ЯМЗ-236М2", "стуки клапанного механизма", 1)[0].page
    q = next(l.strip() for l in hit.text.splitlines() if len(l.strip()) > 40)
    labelled = {"risk": "", "works": [{"title": "Работа", "scope": "specific", "source": "S1", "quote": q}], "parts": []}
    return labelled


def test_failed_recording_never_creates_ticket(index):
    a, toir = make(index, anomaly(status=CheckStatus.check_failed, quality=RecordingQuality.bad), [])
    r = a.run_check("s", "SES-0001")
    assert r["card"]["kind"] == "failed" and r["pending_draft_id"] is None
    a.chat("s", "подтверждаю")
    assert toir.posted == []


def test_bad_quality_blocks_even_with_anomaly(index):
    a, toir = make(index, anomaly(quality=RecordingQuality.bad), [])
    assert a.run_check("s")["card"]["kind"] == "failed"


def test_normal_no_ticket(index):
    a, toir = make(index, anomaly(status=CheckStatus.normal, fault_code=None, fault_title=None), [])
    assert a.run_check("s")["card"]["kind"] == "normal"


def test_ticket_only_after_confirmation(index):
    a, toir = make(index, anomaly(), [good_llm(index)])
    r = a.run_check("s")
    assert r["pending_draft_id"] and toir.posted == []
    r2 = a.chat("s", "подтверждаю")
    assert r2["card"]["kind"] == "ticket" and len(toir.posted) == 1


def test_hallucinated_quote_is_dropped_and_no_ticket(index):
    bad = {"works": [{"title": "Выдумка", "scope": "specific", "source": "S1", "quote": "этой фразы нет ни в одном руководстве совсем"}], "parts": []}
    a, toir = make(index, anomaly(), [bad])
    r = a.run_check("s")
    assert r["card"]["kind"] == "no_basis" and r["pending_draft_id"] is None
    a.chat("s", "подтверждаю")
    assert toir.posted == []


def test_llm_down_falls_back_to_extractive(index):
    a, toir = make(index, anomaly(), [LlmUnavailable("down")])
    r = a.run_check("s")
    assert r["card"]["kind"] == "anomaly" and r["card"]["draft"]["works"]


def test_changed_draft_id_rejected(index):
    a, toir = make(index, anomaly(), [good_llm(index)])
    a.run_check("s")
    r = a.confirm("s", "wrong")
    assert "изменился" in r["text"] and toir.posted == []


def test_validate_rejects_other_engine_page(index):
    a, toir = make(index, anomaly(), [good_llm(index)])
    a.run_check("s")
    s = a.session("s")
    other = next(p for p in index.pages if "ЗМЗ-5234.10" in p.engine_types and len(p.text) > 200)
    s.draft.works[0].source.document = other.document
    s.draft.works[0].source.page = other.page
    assert a.validate_draft(s.vehicle, s.draft)
    assert a.confirm("s", s.draft_id)["card"] is None and toir.posted == []


def test_answer_without_valid_citation_says_not_found(index):
    a, _ = make(index, anomaly(), [])
    a.llm.answers = [{"found": True, "answer": "Момент 99 Нм", "citations": [{"source": "S1", "quote": "полностью выдуманная цитата из руководства"}]}]
    r = a.chat("s", "Какой момент затяжки?")
    assert r["card"]["found"] is False and "99" not in r["text"]
