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
            if "x-if-match" in req.headers and req.headers["x-if-match"] != box.get("etag"):
                return httpx.Response(412, json={"error": {"code": "precondition_failed"}})
            box["v"] = req.content; box["etag"] = f'"{len(box.get("etag", "")) + 1}"' + str(hash(req.content))
            return httpx.Response(200, json={"url": "x"})
        if "v" not in box:
            return httpx.Response(404)
        if req.url.host == "vercel.com":  # head(): metadata with the strong ETag
            return httpx.Response(200, json={"etag": "W/" + box["etag"]})  # Vercel answers the weak form here too
        if req.headers.get("if-none-match") in (box["etag"], "W/" + box["etag"]):
            return httpx.Response(304)
        if box.get("stale"):
            v, tg = box["stale"].pop(0)
            return httpx.Response(200, content=v, headers={"etag": "W/" + tg})
        out = httpx.Response(200, content=box["v"], headers={"etag": "W/" + box["etag"]})  # weak, as the real CDN answers
        hook = box.pop("after_get", None)
        if hook:
            hook()  # another instance writes between this GET and the caller's PUT
        return out
    cl = httpx.Client(transport=httpx.MockTransport(h)); cl.box = box
    return cl


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


def test_concurrent_handoffs_from_two_instances_both_survive(tmp_path):
    """Both instances pulled the same state, then each sent a handoff (same autoincrement id 1) and pushed: none is lost."""
    cli = fake_blob()
    a, b, c = Store(tmp_path / "a.db"), Store(tmp_path / "b.db"), Store(tmp_path / "c.db")
    ra, rb, rc = (Replica(s, token="vercel_blob_rw_STORE1_x") for s in (a, b, c))
    ra.http = rb.http = rc.http = cli
    a.x("INSERT INTO companies(id,name,created) VALUES('c1','Автопарк',0)"); assert ra.push()
    rb.pull(True); ra.pull(True)
    a.add_handoff("u1", "M333YK47", "chief", "с экземпляра A", "", {})
    b.add_handoff("u2", "O819CY198", "chief", "с экземпляра B", "", {})
    b.add_ticket("u2", {"vehicle_id": "O819CY198", "session_id": "S-1", "urgency": "high", "anomaly_type": "fuel"}, "TKT-0002")
    assert ra.push() and rb.push()
    rc.pull(True)
    notes = sorted(h["note"] for h in c.handoffs())
    assert notes == ["с экземпляра A", "с экземпляра B"]
    assert len({h["id"] for h in c.handoffs()}) == 2
    assert [t["ticket_id"] for t in c.tickets()] == ["TKT-0002"]
    assert c.one("SELECT name FROM companies WHERE id='c1'")
    # a delete on one instance is not undone by the other's next push
    ra.pull(True); a.x("DELETE FROM login_tokens"); a.x("INSERT INTO login_tokens(hash,user_id,created,expires) VALUES('h9','u1',0,9e9)"); assert ra.push()
    rb.pull(True); b.x("DELETE FROM login_tokens WHERE hash='h9'"); assert rb.push()
    a.x("UPDATE companies SET note='x'"); assert ra.push()
    rc.pull(True); assert not c.one("SELECT hash FROM login_tokens WHERE hash='h9'")


def test_write_between_read_and_upload_is_not_lost(tmp_path):
    """True race: B reads the shared copy, A uploads its handoff, then B uploads -> B's upload is refused (ETag) and redone."""
    cli = fake_blob()
    a, b, c = Store(tmp_path / "a.db"), Store(tmp_path / "b.db"), Store(tmp_path / "c.db")
    ra, rb, rc = (Replica(s, token="vercel_blob_rw_STORE1_x") for s in (a, b, c))
    ra.http = rb.http = rc.http = cli
    a.x("INSERT INTO companies(id,name,created) VALUES('c1','Автопарк',0)"); assert ra.push()
    ra.pull(True); rb.pull(True)
    a.add_handoff("u1", "M333YK47", "chief", "A", "", {})
    b.add_handoff("u2", "M333YK47", "chief", "B", "", {})
    cli.box["after_get"] = lambda: ra.push()
    assert rb.push()
    assert rb.stats.get("conflicts") == 1
    rc.pull(True)
    assert sorted(h["note"] for h in c.handoffs()) == ["A", "B"]


def test_same_instance_concurrent_requests_keep_unuploaded_rows(tmp_path):
    """One Vercel instance serves requests concurrently: a handoff written while another request uploads, or before a pull,
    must neither be wiped locally nor be skipped by the next push (Vercel: 4 simultaneous handoffs -> 3 seen)."""
    cli = fake_blob()
    a, b, c = Store(tmp_path / "a.db"), Store(tmp_path / "b.db"), Store(tmp_path / "c.db")
    ra, rb, rc = (Replica(s, token="vercel_blob_rw_STORE1_x") for s in (a, b, c))
    ra.http = rb.http = rc.http = cli
    a.x("INSERT INTO companies(id,name,created) VALUES('c1','Автопарк',0)"); assert ra.push()
    rb.pull(True); ra.pull(True)
    a.add_handoff("u1", "M333YK47", "chief", "h1", "", {})
    cli.box["after_get"] = lambda: a.add_handoff("u2", "M333YK47", "chief", "h2", "", {})  # concurrent request on instance A
    assert ra.push()
    assert sorted(h["note"] for h in a.handoffs()) == ["h1", "h2"]  # not wiped by adopting the uploaded copy
    a.add_handoff("u3", "M333YK47", "chief", "h3", "", {})
    b.add_handoff("u4", "M333YK47", "chief", "h4", "", {}); assert rb.push()
    assert ra.pull(True)
    assert sorted(h["note"] for h in a.handoffs()) == ["h1", "h2", "h3", "h4"]  # the pull kept h2/h3 (not uploaded yet)
    assert ra.push()
    rc.pull(True)
    assert sorted(h["note"] for h in c.handoffs()) == ["h1", "h2", "h3", "h4"]
    assert len({h["id"] for h in c.handoffs()}) == 4


def test_stale_content_after_overwrite_is_reread(tmp_path):
    """The blob content lags its metadata after an overwrite: merging into the old copy would drop the other instance's row."""
    cli = fake_blob()
    a, b, c = Store(tmp_path / "a.db"), Store(tmp_path / "b.db"), Store(tmp_path / "c.db")
    ra, rb, rc = (Replica(s, token="vercel_blob_rw_STORE1_x") for s in (a, b, c))
    ra.http = rb.http = rc.http = cli
    a.x("INSERT INTO companies(id,name,created) VALUES('c1','Автопарк',0)"); assert ra.push()
    ra.pull(True); rb.pull(True)
    old_v, old_tag = cli.box["v"], cli.box["etag"]
    a.add_handoff("u1", "M333YK47", "chief", "A", "", {}); assert ra.push()
    cli.box["stale"] = [(old_v, old_tag)] * 2  # the next 2 content reads still answer the previous version
    b.add_handoff("u2", "M333YK47", "chief", "B", "", {}); assert rb.push()
    assert rb.stats.get("stale_reads") == 2
    rc.pull(True)
    assert sorted(h["note"] for h in c.handoffs()) == ["A", "B"]
