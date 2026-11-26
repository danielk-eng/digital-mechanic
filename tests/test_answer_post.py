from digital_mechanic.agent.answer_post import clean


def test_trailing_sources_block_removed():
    t, _ = clean("**Причина:** форсунки [S1].\n\n**Источник**: 236-3902150-Б РЭ [S1], [S2]\n- 236.01.01 РК [S3]")
    assert "Источник" not in t and "[S1]" in t and "[S3]" not in t


def test_offer_questions_become_intents():
    t, intents = clean("Норма 0,25 мм [S1]. Скажите — подготовлю черновик.\n\nСоздать черновик заявки на дефектовку?")
    assert t == "Норма 0,25 мм [S1]." and "ticket" in intents
    t, intents = clean("Шаг выполнен [S2]. Начать ремонт по шагам?")
    assert t == "Шаг выполнен [S2]." and "repair" in intents


def test_real_questions_and_content_kept():
    for s in ("Почему стучит? Причина в клапанах [S1].", "Какой у вас пробег? Это важно [S1]."):
        assert clean(s)[0] == s
