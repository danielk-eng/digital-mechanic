from digital_mechanic.retrieval.expand import expand


def test_procedure_question_is_not_a_symptom():
    qs, sym = expand("Как проверить натяжение ремня генератора?")
    assert not sym and any("натяжения ремней" in q for q in qs)
    qs, sym = expand("Генератор не заряжает, что проверить?")
    assert sym and any("генератор не заряжает" in q for q in qs)
    assert expand("почему стучит двигатель")[1]
