from digital_mechanic.agent.grounded import FactPipeline, _nums

F = [{"label": "S1", "page": 31, "text": "Зазор у впускного и выпускного клапанов 0,25–0,30 мм."},
     {"label": "S2", "page": 270, "text": "Щуп 0,25 мм входит с лёгким нажимом, 0,30 мм — с усилием."},
     {"label": "S2", "page": 270, "text": "Отверните контргайку и выставьте зазор винтом."}]


def check(ans, persona="selftaught", detail="brief"):
    return FactPipeline.check(None, ans, F, {"333"}, persona, detail)


def test_validator_accepts_exact_facts_and_rejects_drift():
    ok = "Зазор 0,25–0,30 мм [S1].\nЩуп 0,25 — легко, 0,30 — с усилием [S2].\nКонтргайку отпусти, винтом выставь [S2]."
    assert check(ok) == []
    assert any("лишние" in p for p in check(ok + "\nМомент 50 Н·м [S2]."))  # a figure not in the facts
    assert any("факт 1" in p for p in check("Щуп 0,25 и 0,30 [S2]. Винтом выставь [S2]."))  # a fact lost
    assert any("метки" in p for p in check(ok.replace("[S1]", "[S7]")))
    five = "\n".join(["Зазор 0,25–0,30 мм [S1]."] * 3 + ["Щуп 0,25 легко, 0,30 с усилием [S2].", "Винт [S2]."])
    assert any("строк" in p for p in check(five, "veteran"))
    assert check("Вчера ты менял форсунку на M333 — как она?\n" + ok, "veteran") == []  # callback line and plate are not facts


def test_plain_fallback_keeps_every_fact_and_four_lines_for_veteran():
    many = F + [{"label": "S3", "page": 28, "text": "Прогиб ремня 10–15 мм."}, {"label": "S3", "page": 28, "text": "Усилие 40 Н."}]
    txt = FactPipeline.plain(many, "veteran")
    assert len(txt.splitlines()) == 4 and all(f"[{f['label']}]" in txt for f in many)
    assert set().union(*(_nums(f["text"]) for f in many)) <= _nums(txt)
