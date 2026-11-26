import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "neurodvs_mock"
sys.path.insert(0, str(VENDOR))

from digital_mechanic.agent.core import Agent  # noqa: E402
from digital_mechanic.clients import Fleet, ServiceError  # noqa: E402
from digital_mechanic.ingest.pages import load_engine_docs, load_pages  # noqa: E402
from digital_mechanic.retrieval.bm25 import ExtraIndex, ManualIndex, load_extra  # noqa: E402
from digital_mechanic.schemas import TicketAck  # noqa: E402
from digital_mechanic.store import Store  # noqa: E402
from digital_mechanic import adapters  # noqa: E402


# The manual indexes are not in the public repo (OEM texts, no redistribution right). Without them only the tests
# that do not need manual pages run; build the index from the task PDFs (docs/ПУБЛИКАЦИЯ.md) to run everything.
INDEX_OK = (ROOT / "data/index/pages.jsonl.gz").is_file()
NO_INDEX_OK = {"test_answer_post", "test_grounding", "test_policy", "test_expand_maint", "test_import_translations", "test_grounded"}


def pytest_collection_modifyitems(config, items):
    if INDEX_OK:
        return
    skip = pytest.mark.skip(reason="no data/index/pages.jsonl.gz: build it from the task PDFs (docs/ПУБЛИКАЦИЯ.md)")
    for it in items:
        if it.module.__name__.rsplit(".", 1)[-1] not in NO_INDEX_OK:
            it.add_marker(skip)


@pytest.fixture(scope="session")
def pages():
    return load_pages(ROOT / "data/index/pages.jsonl.gz", ROOT / "data/engine_docs.yaml")


@pytest.fixture(scope="session")
def index(pages):
    return ManualIndex(pages)


@pytest.fixture(scope="session")
def official_apps():
    from mock_ml.app import app as ml_app
    from mock_toir.app import app as toir_app
    return TestClient(ml_app), TestClient(toir_app)


class MlViaTestClient:
    def __init__(self, tc):
        self.tc = tc

    def diagnose(self, vehicle_id, session_id):
        r = self.tc.post("/diagnose", json={"vehicle_id": vehicle_id, "session_id": session_id})
        if r.status_code != 200:
            raise ServiceError("ml", r.status_code, r.text)
        return adapters.check_from_official(r.json())

    def health(self):
        return True


class ToirViaTestClient:
    def __init__(self, tc):
        self.tc = tc
        self.sent = []

    def create_ticket(self, t):
        body = adapters.ticket_to_official(t)
        self.sent.append(body)
        r = self.tc.post("/tickets", json=body)
        if r.status_code != 201:
            raise ServiceError("toir", r.status_code, r.text)
        return TicketAck(**r.json())

    def health(self):
        return True


def tool_call(name, args, i=0):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


class ScriptedLlm:
    """Plays back assistant messages; a callable item gets the conversation so far."""

    available = True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, step, messages, tools=None, **kw):
        self.calls.append({"step": step, "messages": list(messages), "tools": [t["function"]["name"] for t in tools or []]})
        item = self.script.pop(0) if self.script else SimpleNamespace(content="ok", tool_calls=None)
        if callable(item):
            item = item(messages)
        return item, 10


@pytest.fixture
def make_agent(index, official_apps, tmp_path):
    def _make(script):
        ml_tc, toir_tc = official_apps
        docs = load_engine_docs(ROOT / "data/engine_docs.yaml")["documents"]
        extra = ExtraIndex(load_extra(str(ROOT / "data/index/extra_pages.jsonl.gz")))
        a = Agent(Fleet(ROOT / "data/official/vehicles.json", ROOT / "data/official/fault_types.json"), MlViaTestClient(ml_tc), ToirViaTestClient(toir_tc),
                  index, extra, ScriptedLlm(script), Store(tmp_path / "t.db"), docs)
        a.expand_prefetch = False  # scripted LLM: a background «Подробнее» would take the next scripted reply
        return a
    return _make


def msg(content=None, calls=None):
    return SimpleNamespace(content=content, tool_calls=calls)
