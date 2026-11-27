"""V t565u: repair-intent extraction from noisy workshop transcripts (mixed speakers + a repair phrase)."""
from digital_mechanic.agent.voice_intent import extract, invented_numbers, needs_llm

MIXED = ("Слушай, а когда машину забирать, мне к пяти надо. Да подожди ты. Какой момент затяжки крышек коренных подшипников "
         "на этом ЯМЗ? Ну я ж говорю, к пяти, у меня рейс.")


class FakeLlm:
    available = True

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def json(self, step, system, user, max_tokens=0, on_text=None):
        self.calls.append((step, system, user))
        return self.reply


def test_customer_chatter_dropped_repair_question_kept():
    llm = FakeLlm({"repair_utterance": "Какой момент затяжки крышек коренных подшипников на этом ЯМЗ?",
                   "dropped": ["когда машину забирать, мне к пяти надо", "к пяти, у меня рейс"], "confidence": 0.86})
    it = extract(llm, MIXED, "машина M333YK47 МАЗ, двигатель ЯМЗ-236М2; проверка S-0005: anomaly, F08")
    assert it["utterance"].startswith("Какой момент затяжки") and "к пяти" not in it["utterance"]
    assert len(it["dropped"]) == 2 and it["confirm"] is False and it["raw"] == MIXED
    assert "M333YK47" in llm.calls[0][2] and "ЯМЗ-236М2" in llm.calls[0][2]  # context goes to the model


def test_low_confidence_asks_to_confirm():
    it = extract(FakeLlm({"repair_utterance": "Проверить зазоры клапанов", "dropped": ["шум"], "confidence": 0.45}),
                 "э-э зазоры ну это клапана там шум шум проверить бы, а ты чай будешь? Ну давай потом")
    assert it["confirm"] is True and it["utterance"] == "Проверить зазоры клапанов"


def test_invented_number_never_added():
    raw = "Подожди, Петрович. Какой момент на коренные? Да не, это не тебе. Слышь, какой момент там?"
    it = extract(FakeLlm({"repair_utterance": "Какой момент затяжки коренных — 421 Н·м?", "dropped": ["Подожди, Петрович"], "confidence": 0.9}), raw)
    assert it["invented"] == ["421"] and it["utterance"] == raw and it["confirm"] is True and it["confidence"] <= 0.3
    assert invented_numbers("зазор 0,25 мм", "зазор ноль двадцать пять") == ["0,25"]
    assert invented_numbers("момент 200", "момент двести") == []  # spoken in words
    assert invented_numbers("момент 200", "момент 200 Н·м") == []


def test_offtopic_only():
    it = extract(FakeLlm({"repair_utterance": "", "dropped": ["когда обед", "где ключи от газели"], "confidence": 0.1}),
                 "Когда обед уже? Где ключи от газели, кто брал? Не знаю, спроси у Саныча.")
    assert it.get("offtopic") and it["utterance"] == "" and it["confirm"] is False


def test_short_clean_phrases_skip_the_llm():
    llm = FakeLlm({})
    for raw in ("дальше", "Готово, следующий шаг", "Какой зазор в клапанах и когда его регулировать?"):  # the video's voice question too
        assert not needs_llm(raw)
        it = extract(llm, raw)
        assert it["utterance"] == raw and not it["llm"]
    assert llm.calls == []


def test_clean_long_phrase_keeps_mechanics_words():
    raw = "Подскажи пожалуйста какой момент затяжки болтов крышек коренных подшипников на двигателе ЯМЗ двести тридцать шесть"
    it = extract(FakeLlm({"repair_utterance": "Момент затяжки болтов крышек коренных подшипников ЯМЗ-236?", "dropped": [], "confidence": 0.95}), raw)
    assert it["utterance"] == raw  # nothing dropped and confident: no paraphrase


def test_llm_error_falls_back_to_raw():
    class Bad(FakeLlm):
        def json(self, *a, **k):
            raise RuntimeError("down")
    it = extract(Bad({}), MIXED)
    assert it["utterance"] == MIXED and it["confirm"] is False and it["error"] == "RuntimeError"
