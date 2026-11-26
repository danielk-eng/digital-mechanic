import pytest
from pydantic import ValidationError

from digital_mechanic.schemas import SourcePage, TicketDraft, Priority, WorkItem


def test_ticket_requires_works_and_sources():
    src = SourcePage(document="Руководство по ремонту ЯМЗ-236М2", page=12)
    TicketDraft(
        vehicle_id="A123BC77",
        session_id="SES-0001",
        diagnosis="стук клапанов",
        priority=Priority.medium,
        works=[WorkItem(title="Проверить зазоры клапанов", source=src)],
        parts=[],
        source_pages=[src],
    )


def test_ticket_rejects_empty_works():
    src = SourcePage(document="doc", page=1)
    with pytest.raises(ValidationError):
        TicketDraft(
            vehicle_id="A",
            session_id="S",
            diagnosis="x",
            priority=Priority.low,
            works=[],
            source_pages=[src],
        )
