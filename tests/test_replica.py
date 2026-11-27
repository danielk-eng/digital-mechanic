"""Vercel instances share companies/people/QR links through one private Blob (here: an in-memory fake of the Blob API)."""
import json

import httpx

from digital_mechanic.replica import Replica
from digital_mechanic.store import Store


def fake_blob():
    box = {}

    def h(req: httpx.Request):
        assert req.headers["authorization"].startswith("Bearer ")
        if req.method == "PUT":
            assert req.headers["x-vercel-blob-access"] == "private" and req.headers["x-allow-overwrite"] == "1"
            box["v"] = req.content; box["etag"] = f'"{len(box.get("etag", "")) + 1}"' + str(hash(req.content))
            return httpx.Response(200, json={"url": "x"})
        if "v" not in box:
            return httpx.Response(404)
        if req.headers.get("if-none-match") == box["etag"]:
            return httpx.Response(304)
        return httpx.Response(200, content=box["v"], headers={"etag": box["etag"]})
    return httpx.Client(transport=httpx.MockTransport(h))


def test_two_instances_share_companies_users_and_qr(tmp_path):
    cli = fake_blob()
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")
    ra, rb = Replica(a, token="vercel_blob_rw_STORE1_x"), Replica(b, token="vercel_blob_rw_STORE1_x")
    ra.http = rb.http = cli
    assert ra.on and ra.store_id == "STORE1"
    ra.pull(True); rb.pull(True)
    a.x("INSERT INTO companies(id,name,created) VALUES('c1','Автопарк Б',0)")
    a.x("INSERT INTO company_vehicles VALUES('O819CY198','c1')")
    a.upsert_user("u1", name="Петров Б.", role="manager", company_id="c1", login="petrov1")
    a.x("INSERT INTO login_tokens(hash,user_id,created,expires) VALUES('h1','u1',0,9e9)")
    assert ra.push() and not ra.push()  # second push: nothing changed
    assert rb.pull(True)
    assert b.one("SELECT name FROM companies WHERE id='c1'")["name"] == "Автопарк Б"
    assert b.user("u1")["company_id"] == "c1" and b.one("SELECT user_id FROM login_tokens WHERE hash='h1'")
    # B revokes the QR link -> A sees it
    b.x("UPDATE login_tokens SET revoked=1"); assert rb.push()
    assert ra.pull(True) and a.one("SELECT revoked FROM login_tokens")["revoked"] == 1
    assert not ra.pull(True)  # 304


def test_off_without_token(tmp_path):
    r = Replica(Store(tmp_path / "c.db"), token="")
    assert not r.on and not r.pull(True) and not r.push()


def test_ticket_and_handoff_made_on_one_instance_show_on_another(tmp_path):
    """C1 on Vercel: the mechanic's ticket/handoff hit instance A, the chief's dashboard is served by instance B."""
    cli = fake_blob()
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")
    ra, rb = Replica(a, token="vercel_blob_rw_STORE1_x"), Replica(b, token="vercel_blob_rw_STORE1_x")
    ra.http = rb.http = cli
    ra.pull(True); rb.pull(True)
    a.add_ticket("u1", {"vehicle_id": "M333YK47", "session_id": "S-0005", "urgency": "high", "anomaly_type": "fuel"}, "TKT-0001")
    a.add_handoff("u1", "M333YK47", "chief", "Прошу посмотреть", "сводка", {})
    assert ra.push()
    assert rb.pull(True)
    assert [t["ticket_id"] for t in b.tickets()] == ["TKT-0001"]
    assert len(b.handoffs()) == 1
