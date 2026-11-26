"""Wire settings, data, index, storage and the agent together."""

from __future__ import annotations

from .agent.core import Agent
from .agent.llm import Llm
from .clients import Fleet, MlClient, ToirClient
from .config import Settings, get_settings
from .ingest.pages import load_engine_docs, load_pages
from .retrieval.bm25 import ExtraIndex, ManualIndex, load_extra
from .store import Store


def build_agent(settings: Settings | None = None) -> Agent:
    s = settings or get_settings()
    pages = load_pages(s.index_path, s.engine_docs_path, s.manuals_dir)
    extra = ExtraIndex(load_extra(s.extra_index_path)) if s.extra_kb_enabled else None
    docs = load_engine_docs(s.engine_docs_path)["documents"]
    return Agent(Fleet(s.vehicles_path, s.fault_types_path), MlClient(s.ml_service_url, s.http_timeout_s), ToirClient(s.toir_service_url, s.http_timeout_s),
                 ManualIndex(pages), extra, Llm(s), Store(s.db_path), docs, s.llm_max_tool_rounds, s.pii_masking)
