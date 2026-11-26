"""Fill missing UI strings in web/static/i18n.json via the configured LLM (draft; V reviews).
Usage: PYTHONPATH=src python scripts/translate_i18n.py [lang ...]"""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
from digital_mechanic.agent.llm import Llm
from digital_mechanic.config import get_settings
from digital_mechanic.i18n import LANGS

P = "src/digital_mechanic/web/static/i18n.json"
SYS = ("Ты переводишь строки интерфейса мобильного приложения для механиков автопарка с русского на {name} ({llm}). "
       "Коротко, как на кнопках и в подписях; термины мастерской. Сохрани плейсхолдеры в фигурных скобках ({{law}}, {{n}}), числа, "
       "коды (F01), марки (ЯМЗ), HTML-теги и эмодзи без изменений. Верни JSON-объект с теми же ключами.")
d = json.load(open(P, encoding="utf-8"))
llm = Llm(get_settings())
langs = sys.argv[1:] or [l for l in LANGS if l != "ru"]
for lang in langs:
    d.setdefault(lang, {})
    miss = {k: v for k, v in d["ru"].items() if k not in d[lang]}
    keys = list(miss)
    chunks = [dict((k, miss[k]) for k in keys[i:i + 45]) for i in range(0, len(keys), 45)]
    def run(ch):
        for _ in range(2):
            try:
                r = llm.json("i18n", SYS.format(name=LANGS[lang]["name"], llm=LANGS[lang]["llm"]), json.dumps(ch, ensure_ascii=False), max_tokens=6000)
                ok = {k: v for k, v in r.items() if k in ch and isinstance(v, str) and set(re.findall(r"\{\w+\}", ch[k])) <= set(re.findall(r"\{\w+\}", v))}
                if ok:
                    return ok
            except Exception as e:  # noqa: BLE001
                print(lang, "retry", type(e).__name__)
        return {}
    with ThreadPoolExecutor(4) as ex:
        for part in ex.map(run, chunks):
            d[lang].update(part)
    print(lang, "missing", len(miss), "filled", len([k for k in miss if k in d[lang]]))
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
