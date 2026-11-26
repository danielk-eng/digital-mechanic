from digital_mechanic.agent import profile as P


def test_vocabulary_and_inferred_level():
    p = P.load({})
    for q in ("стучит мотор", "дымит сильно", "не заводится утром"):
        P.update_from_message(p, q)
    assert p["inferred"] == "novice" and not p["terms"]
    p2 = P.load({})
    for q in ("проверил компрессию и тепловой зазор", "ТНВД и угол опережения впрыска", "форсунки на стенде, вкладыши, ГБЦ, коромысла, щуп"):
        P.update_from_message(p2, q)
    assert p2["inferred"] == "expert" and "тнвд" in p2["terms"]
    assert "ТНВД" not in P.prompt_hint(p2, "expert") and "тнвд" in P.prompt_hint(p2, "expert")


def test_novice_gloss_first_times_only_and_not_inside_labels():
    p = P.load({})
    text = "Проверьте форсунку [S1]. Потом снова форсунку и ТНВД [S2]."
    out, done = P.gloss(text, p, "novice")
    assert out.count("(распылитель топлива)") == 1 and "форсунку (распылитель топлива) [S1]" in out and "[S1]" in out
    assert "ТНВД (топливный насос высокого давления)" in out and set(done) == {"форсунка", "тнвд"}
    for _ in range(P.GLOSS_TIMES):
        out, _ = P.gloss(text, p, "novice")
    assert "(распылитель топлива)" not in out  # stops after GLOSS_TIMES
    assert P.gloss(text, P.load({}), "experienced")[0] == text  # no glosses for experienced users
    own = P.load({}); P.update_from_message(own, "менял форсунки")
    assert "(распылитель" not in P.gloss(text, own, "novice")[0]  # he already uses the term


def test_profile_api_edit_and_delete(tmp_path):
    import importlib, json, os
    from fastapi.testclient import TestClient
    os.environ.update(DB_PATH=str(tmp_path / "a.db"), LOG_PATH=str(tmp_path / "l.jsonl"), UPLOADS_DIR=str(tmp_path / "up"), LLM_API_KEY="", LLM_API_KEY_FILE="",
                      ML_SERVICE_URL="http://127.0.0.1:9", TOIR_SERVICE_URL="http://127.0.0.1:9")
    import digital_mechanic.web.app as web
    importlib.reload(web)
    web.STORE.upsert_user("u-prof", name="A", role="mechanic", consent_at=1.0,
                          profile=json.dumps({"terms": {"тнвд": 2, "щуп": 1}, "glossed": {}, "messages": 4, "expands": 0}))
    c = TestClient(web.app); c.cookies.set("s", web.session_cookie("u-prof"))
    assert c.get("/api/me").json()["profile"]["terms"] == ["тнвд", "щуп"]
    assert c.post("/api/me/profile", json={"remove_term": "щуп"}).json()["profile"]["terms"] == ["тнвд"]
    assert c.post("/api/me/profile", json={"reset": True}).json()["profile"]["terms"] == []
    c.delete("/api/me")
    assert web.STORE.user("u-prof") is None  # deleted with the user's data


def test_expert_does_not_get_own_abbreviations_explained():
    assert P.terms_in("гбц") and P.terms_in("ТНВД") and not P.terms_in("дымит")
