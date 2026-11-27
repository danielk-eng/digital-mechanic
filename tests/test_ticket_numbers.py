"""Embedded TOiR mock (Vercel): a new process must not reissue TKT-0001 when the shared store already has it."""
import itertools
import sys

from digital_mechanic.bootstrap import _embedded_mocks, _seed_ticket_numbers
from digital_mechanic.config import Settings
from digital_mechanic.schemas import Reference, TicketRequest


class _Store:
    def __init__(self, ids):
        self.ids = ids

    def tickets(self, vehicle_id=None):
        return [{"ticket_id": i} for i in self.ids]


class _Agent:
    pass


def test_new_process_continues_ticket_numbers_from_the_store():
    ml, toir = _embedded_mocks(Settings(embed_mocks=True))
    mod = sys.modules["mock_toir.app"]
    mod._counter = itertools.count(1)  # a fresh process
    a = _Agent(); a.toir, a.store = toir, _Store(["TKT-0001", "TKT-0007", "TKT-DEMO-112"])
    _seed_ticket_numbers(a)
    req = TicketRequest(vehicle_id="M333YK47", session_id="S-0005", anomaly_type="F08", conclusion="Жёсткая работа", urgency="medium",
                        operations=["Проверить зазоры"], spare_parts=[], references=[Reference(manual="236.01.01 РК", pdf_page=269)])
    assert toir.create_ticket(req).ticket_id == "TKT-0008"
    a.store.ids.append("TKT-0008")
    assert toir.create_ticket(req).ticket_id == "TKT-0009"
