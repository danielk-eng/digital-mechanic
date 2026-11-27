"""Personal data never leaves the server in clear text (152-FZ).

Two uses:
* `Masker` -- reversible. Every text sent to an external model (LLM at TokenRouter / Cloud.ru, translation, extracts) is
  depersonalized: names, phones, e-mails, licence plates, VINs, passport / SNILS / INN / card numbers are replaced with
  placeholders «[ФИО_1]», «[ТЕЛ_1]», «[ГОСНОМЕР_1]», «[VIN_1]» … The mapping lives only in memory for one request; the
  answer (and every streamed chunk) gets the real values back on our server.
* `mask()` -- irreversible, for what we store and log (audit, chat history, ticket text): the same detector, tags without
  numbers («[ФИО]», «[ТЕЛ]»), so stored text never collides with the placeholders of a later request.

Local and deterministic: regular expressions + a dictionary of common first names / patronymic and surname endings. No model,
no network, a few milliseconds on a 12 000-character prompt. Engine and vehicle models (ЯМЗ-536, КАМАЗ-65115, ПАЗ-3204), part
numbers, fault codes (P0101, SPN 3251 FMI 2, F03), torques and clearances are not touched (tests: tests/test_pii.py).
"""

from __future__ import annotations

import re
import threading
import time

# ---------------------------------------------------------------- detectors
_PLATE_L = "АВЕКМНОРСТУХABEKMHOPCTYX"
_L = f"[{_PLATE_L}{_PLATE_L.lower()}]"
# RU licence plate: letter, 3 digits, 2 letters, region 2-3 digits (M333YK47, м851та178, «А 123 ВС 77», «А123ВС 777 RUS»)
_PLATE = re.compile(rf"(?<![\w-]){_L}\s?\d{{3}}\s?{_L}{{2}}\s?\d{{2,3}}(?:\s?(?:RUS|rus))?(?![\w-])")
# VIN: 17 characters, no I/O/Q, at least two letters, the last four are digits (serial number): XTC65115081234567
_VIN = re.compile(r"(?<![A-Za-z0-9])(?=[A-HJ-NPR-Z0-9]{13}\d{4}(?![A-Za-z0-9]))(?=(?:[0-9]*[A-HJ-NPR-Z]){2})[A-HJ-NPR-Z0-9]{17}")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-zА-Яа-я]{2,}")
# phones: +7 / 8 / 7 with the usual separators; a bare 10/11-digit run only right after «тел» / «телефон» / «моб»
_PHONE = re.compile(r"(?<![\w+.-])\+\s?7[\s-]?(?:\(\d{3}\)|\d{3})[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d|[.-]\d)"     # +7 912 345-67-89
                    r"|(?<![\w+.-])\+\s?7[\s-]?\(\d{4,5}\)[\s-]?\d{1,2}-?\d{2}-\d{2}(?!\d|[.-]\d)"                 # +7 (4852) 27-46-81
                    r"|(?<![\w+.-])[78][\s-]?\(\d{3}\)[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d|[.-]\d)"             # 8 (912) 345-67-89
                    r"|(?<![\w+.-])[78][\s-]\d{3}[\s-]\d{3}[\s-]\d{2}[\s-]\d{2}(?!\d|[.-]\d)"                       # 8-912-345-67-89
                    r"|(?<![\w+.-])[78]9\d{9}(?!\d|[.-]\d)"                                                       # 89123456789
                    r"|(?<![\w+.-])\+(?:9[89]\d|7[67]\d|37[45]|99[2-8]|8[4]|9[1]|88[0])[\s-]?\(?\d{2,4}\)?[\s-]?\d{2,4}[\s-]?\d{2,4}(?:[\s-]?\d{2,4})?(?!\d|[.-]\d)")  # UZ/KZ/TJ/KG/BY/VN/IN/BD
_PHONE_KW = re.compile(r"(?i)((?:тел(?:ефон)?|моб(?:ильный)?|phone|tel)\.?[\s:№#]*)(\d[\d\s()-]{8,16}\d)")
_SNILS = re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[\s-]\d{2}(?!\d)")
_SNILS_KW = re.compile(r"(?i)(снилс[\s:№#]*)(\d{11}|\d{3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{2})(?!\d)")
_PASSPORT = re.compile(r"(?i)(паспорт\w*(?:\s+(?:серия|сер\.|рф|гражданина\s+рф))?[\s:№#]*)(\d{2}\s?\d{2}(?:\s*(?:№|номер|n)?\s*)\d{6})(?!\d)")
_PASSPORT_SN = re.compile(r"(?i)(серия\s*)(\d{2}\s?\d{2})(\s*(?:№|номер)\s*)(\d{6})(?!\d)")
_INN = re.compile(r"(?i)((?:ИНН|inn)[\s:№#]*)(\d{12}|\d{10})(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)")
_CARD_KW = re.compile(r"(?i)(?:карт\w*|card|счёт\w*|счет\w*)[\s:№#]*$")


def _alone(text: str, a: int, b: int) -> bool:
    """Not one cell of a row of numbers (tables of masses, dimensions, part numbers)."""
    return not re.search(r"\d\s{1,3}$", text[max(0, a - 4):a]) and not re.match(r"\s{1,3}\d", text[b:b + 4])

# names --------------------------------------------------------------
_FIRST = """Александр Алексей Анатолий Андрей Антон Аркадий Арсений Артём Артем Артур Богдан Борис Вадим Валентин Валерий Василий
Виктор Виталий Владимир Владислав Всеволод Вячеслав Геннадий Георгий Герман Глеб Григорий Давид Даниил Данил Денис Дмитрий Евгений
Егор Захар Иван Игнат Игорь Илья Кирилл Константин Леонид Макар Максим Марат Марк Матвей Михаил Никита Николай Олег Павел Пётр Петр
Ренат Родион Руслан Рустам Савелий Семён Семен Сергей Станислав Степан Тагир Тимофей Тимур Фёдор Федор Филипп Эдуард Юрий Ярослав
Азамат Алишер Бахтиёр Бахтияр Джамшид Ильдар Ильнур Камиль Нурлан Рашид Тимурбек Улугбек Фарход Шамиль Шерзод Ержан Даурен Айбек
Александра Алёна Алена Алина Алла Анастасия Анна Валентина Валерия Варвара Галина Дарья Диана Евгения Екатерина Елена Елизавета
Жанна Зинаида Зоя Инна Ирина Карина Кристина Ксения Лариса Лидия Людмила Марина Мария Наталья Наталия Нина Оксана Олеся Ольга Полина
Светлана Софья София Таисия Тамара Татьяна Ульяна Юлия Яна Гульнара Дильноза Айгуль Зарина""".split()
# Вера, Надежда, Любовь, Лев, Роман, Слава, Мир… are ordinary words too: matched only next to a surname / patronymic / initials


def _forms(n: str) -> list[str]:
    if n.endswith("й"):
        b = n[:-1]
        return [n, b + "я", b + "ю", b + "ем", b + "е"]
    if n.endswith("ь"):
        b = n[:-1]
        return [n, b + "я", b + "ю", b + "ем", b + "е"]
    if n.endswith("я"):
        b = n[:-1]
        return [n, b + "и", b + "е", b + "ю", b + "ей"]
    if n.endswith("а"):
        b = n[:-1]
        return [n, b + "ы", b + "и", b + "е", b + "у", b + "ой"]
    return [n, n + "а", n + "у", n + "ом", n + "е"]


_FIRST_FORMS = sorted({f for n in _FIRST for f in _forms(n)} | {"Вера", "Надежда", "Любовь", "Лев", "Роман"}, key=len, reverse=True)
_AMBIG = {"Вера", "Надежда", "Любовь", "Лев", "Роман"} | set(_forms("Марк")) | set(_forms("Алина"))  # «Марка масла», OCR «Алиной» = «длиной»
_CAP = r"[А-ЯЁ][а-яё]+"
_SURN = r"[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ский|цкий|ской|цкой|ская|цкая|ова|ева|ёва|ина|ына|енко|ук|юк|ян|дзе|швили|ич|их|ых)(?:а|у|ым|ой|е|ым|ых|ого|ому|им)?"
_PATR = r"[А-ЯЁ][а-яё]+(?:ович|евич|ьич|ична|овна|евна|инична)(?:а|у|ем|ем|е|ой|ы)?"
_INIT = r"[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?"
_ROLE = r"(?:механик\w*|водител\w*|мастер\w*|слесар\w*|инженер\w*|диспетчер\w*|начальник\w*|бригадир\w*|сотрудник\w*|коллег\w*|товарищ\w*|гражданин\w*|клиент\w*|владел\w*|г-н|г-жа|господин\w*)"
_FIRST_SET = frozenset(_FIRST_FORMS)
# (regex, which groups must be a known first name) -- the names are checked against a set, not a 600-way alternation (4 ms -> 0.3 ms)
_NAME_RXS = [
    (re.compile(rf"(?<![\w.]){_CAP}\s+{_CAP}\s+{_PATR}(?!\w)"), ()),                 # Иванов Иван Иванович
    (re.compile(rf"(?<![\w.]){_CAP}\s+{_PATR}(?:\s+{_SURN})?(?!\w)"), ()),            # Иван Петрович [Сидоров]
    (re.compile(rf"(?<![\w.]){_SURN}\s+{_INIT}(?![\w])"), ()),                       # Иванов И.И. / Петров Б.
    (re.compile(rf"(?<![\w.]){_INIT}\s?{_SURN}(?!\w)"), ()),                          # И.И. Иванов
    (re.compile(rf"(?<![\w.])({_CAP})\s+{_SURN}(?!\w)"), (1,)),                       # Борис Петров
    (re.compile(rf"(?<![\w.]){_SURN}\s+({_CAP})(?!\w)"), (1,)),                       # Петров Борис
    (re.compile(rf"(?i:(?<!\w){_ROLE})\s+({_SURN}|{_CAP})(?!\w)"), ("role",)),        # механик Петров / водителю Сергею
    (re.compile(rf"(?<![\w.])({_CAP})(?!\w)"), ("alone",)),                                  # a common first name alone («Сергей, проверь»)
]

# kind -> placeholder tag
TAGS = {"name": "ФИО", "phone": "ТЕЛ", "email": "EMAIL", "plate": "ГОСНОМЕР", "vin": "VIN", "passport": "ПАСПОРТ",
        "snils": "СНИЛС", "inn": "ИНН", "card": "КАРТА"}
_PH = re.compile(r"\[\s*(ФИО|ТЕЛ|EMAIL|ГОСНОМЕР|VIN|ПАСПОРТ|СНИЛС|ИНН|КАРТА)[_ ](\d{1,3})\s*\]")
_PH_PARTIAL = re.compile(r"\[\s*(?:[А-ЯЁA-Z]{0,9}(?:[_ ]\d{0,3})?)?\s*$")
_TAG_ANY = re.compile(r"\[(?:ФИО|ТЕЛ|EMAIL|ГОСНОМЕР|VIN|ПАСПОРТ|СНИЛС|ИНН|КАРТА)(?:_\d{1,3})?\]")


def _luhn(d: str) -> bool:
    s, alt = 0, False
    for ch in reversed(d):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        s, alt = s + n, not alt
    return s % 10 == 0


def find(text: str) -> list[tuple[int, int, str]]:
    """Spans (start, end, kind) of personal data, non-overlapping, left to right."""
    spans: list[tuple[int, int, str]] = []
    taken = [False] * (len(text) + 1)

    def add(a: int, b: int, kind: str) -> None:
        if a >= b or any(taken[a:b]):
            return
        if _TAG_ANY.search(text[max(0, a - 1):b + 1]):
            return
        for i in range(a, b):
            taken[i] = True
        spans.append((a, b, kind))

    for m in _TAG_ANY.finditer(text):  # never mask our own placeholders / tags
        for i in range(m.start(), m.end()):
            taken[i] = True
    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), "email")
    for rx, kind in ((_PASSPORT, "passport"), (_INN, "inn"), (_SNILS_KW, "snils"), (_PHONE_KW, "phone")):
        for m in rx.finditer(text):
            add(m.start(2), m.end(2), kind)
    for m in _PASSPORT_SN.finditer(text):
        add(m.start(2), m.end(4), "passport")
    for m in _SNILS.finditer(text):
        add(m.start(), m.end(), "snils")
    for m in _CARD.finditer(text):  # «карта 4276 …» always; otherwise a Luhn-valid number standing alone (not a table row)
        kw = _CARD_KW.search(text[max(0, m.start() - 40):m.start()])
        if kw or (_luhn(re.sub(r"\D", "", m.group())) and _alone(text, m.start(), m.end())):
            add(m.start(), m.end(), "card")
    for m in _VIN.finditer(text):
        add(m.start(), m.end(), "vin")
    for m in _PLATE.finditer(text):
        add(m.start(), m.end(), "plate")
    for m in _PHONE.finditer(text):
        if _alone(text, m.start(), m.end()):
            add(m.start(), m.end(), "phone")
    surn = re.compile(rf"{_SURN}")
    for rx, need in _NAME_RXS:
        for m in rx.finditer(text):
            if need == ("role",):  # only the name after the role word; a surname or a known first name
                w = m.group(1)
                if w in _AMBIG or not (w in _FIRST_SET or surn.fullmatch(w)):
                    continue
                add(m.start(1), m.end(1), "name")
                continue
            if need == ("alone",):  # a first name alone: only unambiguous ones («Роман», «Вера», «Марка» are words too)
                if m.group(1) in _FIRST_SET and m.group(1) not in _AMBIG:
                    add(m.start(1), m.end(1), "name")
                continue
            if any(m.group(g) not in _FIRST_SET for g in need):  # next to a surname even «Роман Петров» is a name
                continue
            add(m.start(), m.end(), "name")
    spans.sort()
    return spans


def _key(kind: str, v: str) -> str:
    v = v.strip()
    if kind in ("phone", "card", "snils", "inn", "passport"):
        return re.sub(r"\D", "", v)[-10:]
    if kind in ("plate", "vin"):
        return re.sub(r"\s|RUS|rus", "", v).upper().translate(str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX"))
    return re.sub(r"\s+", " ", v).lower()


# ---------------------------------------------------------------- stats (engineer panel, METRICS.md)
class _Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls = self.entities = 0
        self.ms_total = self.ms_max = 0.0
        self.by_kind: dict[str, int] = {}

    def add(self, ms: float, kinds: list[str]) -> None:
        with self.lock:
            self.calls += 1
            self.entities += len(kinds)
            self.ms_total += ms
            self.ms_max = max(self.ms_max, ms)
            for k in kinds:
                self.by_kind[k] = self.by_kind.get(k, 0) + 1

    def as_dict(self) -> dict:
        with self.lock:
            return {"calls": self.calls, "entities": self.entities, "by_kind": dict(self.by_kind),
                    "avg_ms": round(self.ms_total / self.calls, 2) if self.calls else 0.0, "max_ms": round(self.ms_max, 2)}


STATS = _Stats()


class Masker:
    """Reversible masking for one request: the same value always gets the same placeholder."""

    def __init__(self) -> None:
        self.fwd: dict[tuple[str, str], str] = {}
        self.back: dict[str, str] = {}
        self.n: dict[str, int] = {}

    def _ph(self, kind: str, v: str) -> str:
        k = (kind, _key(kind, v))
        if k not in self.fwd:
            self.n[kind] = self.n.get(kind, 0) + 1
            ph = f"[{TAGS[kind]}_{self.n[kind]}]"
            self.fwd[k] = ph
            self.back[ph] = v
        return self.fwd[k]

    def mask(self, text: str | None) -> str:
        if not text:
            return text or ""
        t0 = time.perf_counter()
        spans = find(text)
        out, last = [], 0
        for a, b, kind in spans:
            out.append(text[last:a])
            out.append(self._ph(kind, text[a:b]))
            last = b
        out.append(text[last:])
        STATS.add((time.perf_counter() - t0) * 1000, [k for _, _, k in spans])
        return "".join(out)

    def unmask(self, text: str | None) -> str:
        if not text or not self.back:
            return text or ""

        def rep(m: re.Match) -> str:
            return self.back.get(f"[{m.group(1)}_{int(m.group(2))}]", m.group(0))
        return _PH.sub(rep, text)

    def unmask_partial(self, text: str) -> str:
        """For a streamed answer (cumulative text): restore complete placeholders and hold back a trailing «[ФИ» / «[ТЕЛ_» that
        may still become one, so the screen never shows half a placeholder."""
        if not text or not self.back:
            return text or ""
        i = text.rfind("[")
        if i >= 0 and "]" not in text[i:] and len(text) - i <= 16 and _PH_PARTIAL.fullmatch(text[i:]):
            text = text[:i]
        return self.unmask(text)

    def mask_messages(self, messages: list) -> list:
        """Copy of chat messages with every text part masked (system prompt, history, tool results, tool-call arguments)."""
        out = []
        for m in messages:
            if not isinstance(m, dict):
                out.append(m)
                continue
            m2 = dict(m)
            c = m2.get("content")
            if isinstance(c, str):
                m2["content"] = self.mask(c)
            elif isinstance(c, list):
                m2["content"] = [({**p, "text": self.mask(p.get("text"))} if isinstance(p, dict) and p.get("type") == "text" else p) for p in c]
            if m2.get("tool_calls"):
                tcs = []
                for tc in m2["tool_calls"]:
                    if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                        tc = {**tc, "function": {**tc["function"], "arguments": self.mask(tc["function"].get("arguments") or "")}}
                    tcs.append(tc)
                m2["tool_calls"] = tcs
            out.append(m2)
        return out


_STORE_TAG = {k: f"[{v}]" for k, v in TAGS.items()}


def mask(text: str | None, keep_names: bool = False) -> str:
    """Irreversible masking for storage and logs: «Иванов И.И., +7 912 …» -> «[ФИО], [ТЕЛ] …».
    keep_names: a person's own display name / style card («Петров Б.», «зови меня Сергей») stays -- it is shown in the
    app; on its way to the model it is still replaced by Masker.
    Licence plates and VINs stay in what we store: they are the fleet's keys (tickets, history, audit) and belong to the
    company's trucks; they are hidden only from the external model."""
    if not text:
        return text or ""
    spans = [s for s in find(text) if s[2] not in ("plate", "vin") and not (keep_names and s[2] == "name")]
    out, last = [], 0
    for a, b, kind in spans:
        out.append(text[last:a])
        out.append(_STORE_TAG[kind])
        last = b
    out.append(text[last:])
    return "".join(out)
