"""Per-user adaptive profile (stored in users.profile as JSON, only after consent; deleted with «Удалить мои данные»).

- vocabulary: professional terms the mechanic uses himself -> the agent answers in the same terms;
- inferred level: from how many distinct professional terms he uses (shown in settings; the explicit setting wins);
- glosses for novices: the first few times a term appears in an answer, a short plain gloss in brackets,
  e.g. «форсунка (распылитель топлива)». No lectures: one bracket, first occurrence only, at most GLOSS_TIMES per term.
"""
from __future__ import annotations

import json
import re
import time

GLOSS_TIMES = 3
MAX_GLOSSES_PER_ANSWER = 3

# term id -> (regex of the term in Russian incl. inflections, gloss for a novice)
GLOSSARY: dict[str, tuple[str, str]] = {
    "форсунка": (r"форсунк\w*", "распылитель топлива"),
    "тнвд": (r"ТНВД", "топливный насос высокого давления"),
    "плунжер": (r"плунжер\w*", "поршенёк насоса, который качает топливо"),
    "распылитель": (r"распылител\w*", "наконечник форсунки с отверстиями"),
    "угол опережения": (r"угл\w* опережени\w* впрыск\w*", "момент подачи топлива до верхней точки поршня"),
    "турбокомпрессор": (r"турбокомпрессор\w*|турбин\w* наддув\w*", "турбина, нагнетающая воздух"),
    "интеркулер": (r"интеркулер\w*|охладител\w* наддувочного воздуха", "радиатор воздуха после турбины"),
    "гбц": (r"ГБЦ|головк\w* блока цилиндров", "головка блока: крышка цилиндров с клапанами"),
    "цпг": (r"ЦПГ", "цилиндры, поршни и кольца"),
    "кшм": (r"КШМ", "коленвал, шатуны и поршни"),
    "грм": (r"ГРМ", "механизм, открывающий клапаны"),
    "коромысло": (r"коромысл\w*", "рычаг, нажимающий на клапан"),
    "толкатель": (r"толкател\w*", "стержень от распредвала к коромыслу"),
    "гидрокомпенсатор": (r"гидрокомпенсатор\w*|гидротолкател\w*", "толкатель, сам выбирающий зазор"),
    "тепловой зазор": (r"теплов\w* зазор\w*|зазор\w* в клапан\w*", "щель между коромыслом и клапаном"),
    "распредвал": (r"распределительн\w* вал\w*|распредвал\w*", "вал, открывающий клапаны"),
    "коленвал": (r"коленчат\w* вал\w*|коленвал\w*", "главный вал двигателя"),
    "вкладыш": (r"вкладыш\w*", "тонкая втулка-подшипник вала"),
    "поршневые кольца": (r"поршнев\w* кол\w*", "кольца на поршне, держат компрессию"),
    "компрессия": (r"компресси\w*", "давление в цилиндре при сжатии"),
    "картерные газы": (r"картерн\w* газ\w*", "газы, прорвавшиеся в картер мимо колец"),
    "сальник": (r"сальник\w*", "уплотнение вала"),
    "фильтр тонкой очистки": (r"фильтр\w* тонкой очистки", "второй, мелкий фильтр топлива"),
    "топливоподкачивающий насос": (r"топливоподкачивающ\w* насос\w*", "насос, подающий топливо из бака"),
    "подсос воздуха": (r"подсос\w* воздух\w*", "воздух попадает в топливную систему"),
    "прокачка": (r"прокачать|прокачк\w*", "выгнать воздух из топливной системы"),
    "дефектовка": (r"дефектовк\w*", "осмотр и замер деталей, чтобы найти неисправность"),
    "динамометрический ключ": (r"динамометрическ\w* ключ\w*", "ключ, показывающий силу затяжки"),
    "момент затяжки": (r"момент\w* затяжк\w*", "с какой силой затягивать болт"),
    "щуп": (r"щуп\w*", "пластинка для замера зазора"),
    "стетоскоп": (r"стетоскоп\w*", "трубка, чтобы прослушать стук"),
    "детонация": (r"детонаци\w*", "взрывное сгорание, звонкий стук"),
}
_RX = {k: re.compile(r"(?<![\w-])(" + rx + r")(?![\w-])", re.I) for k, (rx, _) in GLOSSARY.items()}


def load(user: dict) -> dict:
    try:
        p = json.loads(user.get("profile") or "{}")
    except (TypeError, ValueError):
        p = {}
    p.setdefault("terms", {})
    p.setdefault("glossed", {})
    p.setdefault("messages", 0)
    p.setdefault("expands", 0)
    return p


def dump(p: dict) -> str:
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"))


def terms_in(text: str) -> list[str]:
    return [k for k, rx in _RX.items() if rx.search(text or "")]


def inferred_level(p: dict) -> str:
    n = len([k for k, v in p["terms"].items() if v >= 1])
    if p["messages"] < 3:
        return ""  # too early to say
    return "expert" if n >= 8 else "experienced" if n >= 3 else "novice"


def update_from_message(p: dict, text: str, event: str | None = None) -> dict:
    if event == "expand":
        p["expands"] += 1
    elif text and not event:
        p["messages"] += 1
        for k in terms_in(text):
            p["terms"][k] = p["terms"].get(k, 0) + 1
    p["inferred"] = inferred_level(p)
    p["prefers_detail"] = p["expands"] >= 3 and p["expands"] >= p["messages"] / 2
    p["updated"] = int(time.time())
    return p


def prompt_hint(p: dict, level: str) -> str:
    """Extra line for the system prompt."""
    own = sorted(p["terms"], key=lambda k: -p["terms"][k])[:12]
    if level == "novice":
        return ("Профессиональные термины вводи постепенно и называй деталь так же, как механик; пояснения к терминам "
                "добавит интерфейс — сам их в скобках не пиши.") + (f" Механик уже сам употребляет: {', '.join(own)}." if own else "")
    if own:
        return f"Механик сам употребляет термины: {', '.join(own)} — отвечай в этих же терминах, без пояснений азов."
    return ""


def gloss(text: str, p: dict, level: str) -> tuple[str, list[str]]:
    """Novice: add «(gloss)» after the first occurrence of a term he has not used himself and has seen < GLOSS_TIMES times."""
    if level != "novice" or not text:
        return text, []
    done: list[str] = []
    for k, rx in _RX.items():
        if len(done) >= MAX_GLOSSES_PER_ANSWER:
            break
        if p["terms"].get(k) or p["glossed"].get(k, 0) >= GLOSS_TIMES:
            continue
        for m in rx.finditer(text):
            before, after = text[:m.start()], text[m.end():]
            if before.count("[") > before.count("]") or before.count("(") > before.count(")") or before.count("**") % 2 or after.lstrip().startswith("("):
                continue  # inside a label / bold, or already explained
            text = text[:m.end()] + f" ({GLOSSARY[k][1]})" + text[m.end():]
            p["glossed"][k] = p["glossed"].get(k, 0) + 1
            done.append(k)
            break
    return text, done


def public(p: dict) -> dict:
    """What settings show (and the user can edit/remove)."""
    return {"terms": sorted(p["terms"], key=lambda k: -p["terms"][k]), "inferred": p.get("inferred", ""), "messages": p["messages"],
            "prefers_detail": bool(p.get("prefers_detail")), "glossed": sorted(p["glossed"])}
