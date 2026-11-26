"""Wire settings, data, index and agent together."""

from __future__ import annotations

from .agent.llm import Llm
from .agent.orchestrator import MechanicAgent
from .clients import Fleet, MlClient, ToirClient
from .config import Settings, get_settings
from .ingest.pages import ingest_manuals
from .retrieval.bm25 import ManualIndex


def build_agent(settings: Settings | None = None) -> MechanicAgent:
    s = settings or get_settings()
    pages = ingest_manuals(s.manuals_dir, s.engine_docs_path, s.cache_dir, ocr=s.ocr_enabled)
    return MechanicAgent(Fleet(s.vehicles_path), MlClient(s.ml_service_url), ToirClient(s.toir_service_url), ManualIndex(pages), Llm(s), s.trace_dir)
