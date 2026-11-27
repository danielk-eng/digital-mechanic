"""Wire settings, data, index, storage and the agent together."""

from __future__ import annotations

from .agent.core import Agent
from .agent.llm import Llm
from .clients import Fleet, MlClient, ToirClient
from .config import Settings, get_settings
from .ingest.pages import load_engine_docs, load_pages
from .retrieval.bm25 import ExtraIndex, ManualIndex, load_extra
from .store import Store


def _embedded_mocks(s: Settings):
    """In-process official mocks (no network hop); used only when EMBED_MOCKS=1."""
    import sys
    from pathlib import Path

    from starlette.testclient import TestClient

    root = str(Path(s.mocks_dir).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from mock_ml.app import app as ml_app  # type: ignore
    from mock_toir.app import app as toir_app  # type: ignore

    return (MlClient("http://mock-ml", s.http_timeout_s, TestClient(ml_app, base_url="http://mock-ml")),
            ToirClient("http://mock-toir", s.http_timeout_s, TestClient(toir_app, base_url="http://mock-toir")))


def _seed_ticket_numbers(agent) -> None:
    """The embedded TOiR mock numbers tickets from TKT-0001 in every process; on Vercel every instance and every deploy is a
    new process while the tickets live in the shared store — the chief saw TKT-0001 three times. Before a ticket is created
    the mock's counter is moved past the largest number already known."""
    import itertools
    import re
    import sys

    mod = sys.modules.get("mock_toir.app")
    if mod is None or not hasattr(mod, "_counter"):
        return

    def seed() -> None:
        nums = [int(m.group(1)) for t in agent.store.tickets() for m in [re.fullmatch(r"TKT-(\d+)", str(t.get("ticket_id") or ""))] if m]
        nums += [int(m.group(1)) for t in getattr(mod, "TICKETS", []) for m in [re.fullmatch(r"TKT-(\d+)", str(t.get("ticket_id") or ""))] if m]
        if nums:
            with mod._lock:
                mod._counter = itertools.count(max(nums) + 1)

    agent.toir.before_create = seed


def build_agent(settings: Settings | None = None) -> Agent:
    s = settings or get_settings()
    if s.embed_mocks:
        ml, toir = _embedded_mocks(s)
    else:
        ml, toir = MlClient(s.ml_service_url, s.http_timeout_s), ToirClient(s.toir_service_url, s.http_timeout_s)
    pages = load_pages(s.index_path, s.engine_docs_path, s.manuals_dir)
    extra = ExtraIndex(load_extra(s.extra_index_path)) if s.extra_kb_enabled else None
    docs = load_engine_docs(s.engine_docs_path)["documents"]
    agent = Agent(Fleet(s.vehicles_path, s.fault_types_path), ml, toir,
                 ManualIndex(pages), extra, Llm(s), Store(s.db_path), docs, s.llm_max_tool_rounds, s.pii_masking)
    if s.embed_mocks:
        _seed_ticket_numbers(agent)
    from .history import load_demo

    agent.demo = load_demo(s.demo_history_path) if s.demo_history_path else {}
    return agent
