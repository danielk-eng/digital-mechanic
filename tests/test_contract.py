"""Our ticket JSON must pass the official Mock TOiR (TZ v6 section 5); no legacy fields (HTTP 418)."""
from digital_mechanic import adapters
from digital_mechanic.schemas import Reference, TicketRequest


def test_ticket_accepted_by_official_mock(official_apps):
    _, toir = official_apps
    t = TicketRequest(vehicle_id="M333YK47", session_id="S-0005", anomaly_type="F08", conclusion="Жёсткая работа", urgency="medium",
                      operations=["Проверить зазоры"], spare_parts=[], references=[Reference(manual="236.01.01 РК", pdf_page=269)])
    r = toir.post("/tickets", json=adapters.ticket_to_official(t))
    assert r.status_code == 201 and r.json()["ticket_id"].startswith("TKT-")


def test_diagnose_contract(official_apps):
    ml, _ = official_apps
    c = adapters.check_from_official(ml.post("/diagnose", json={"vehicle_id": "M333YK47", "session_id": "S-0005"}).json())
    assert c.status.value == "anomaly" and c.anomaly_type == "F08"


def test_embedded_mocks_bootstrap(tmp_path, monkeypatch):
    from digital_mechanic.bootstrap import _embedded_mocks
    from digital_mechanic.config import Settings
    ml, toir = _embedded_mocks(Settings(embed_mocks=True))
    assert ml.health() and toir.health()
    assert ml.diagnose("M333YK47", "S-0005").status == "anomaly"
