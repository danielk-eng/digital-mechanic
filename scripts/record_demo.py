"""Record the current demo UI with Playwright.

  python scripts/record_demo.py --url http://127.0.0.1:18610 --out runs/video
  python scripts/record_demo.py --url http://127.0.0.1:18610 --smoke

The mobile mechanic part is recorded at 430x860, manager/engineer at 1280x720. The script writes raw WebM files,
accelerated parts, a concatenated silent draft MP4, screenshots and markers.json. It types the voice question: use a
real microphone pass for the final narrated competition video.
"""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MAIN = ("B418HK98", "S-0009")
LOWCONF = ("M739BA198", "S-0103")
MISMATCH = ("Y652CK98", "S-0132")
RESUME = "E549YT198"
Q_VOICE = "Какой зазор в клапанах и когда его регулировать?"
Q_NODATA = "Какой момент затяжки гаек крепления турбокомпрессора?"
TICKET = "Создай заявку на ремонт"


def browser_executable() -> str | None:
    for p in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"):
        if os.path.exists(p):
            return p
    return None


class Marks:
    def __init__(self):
        self.t0 = time.monotonic()
        self.events = []
        self.speed = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    def event(self, name: str) -> None:
        self.events.append({"t": round(self.now(), 2), "event": name})

    def wait(self, name: str, start: float, end: float) -> None:
        dur = end - start
        if dur < 6:
            return
        factor = 5 if dur >= 30 else 4 if dur >= 15 else 3
        self.speed.append({"name": name, "start": round(max(0.0, start - 0.4), 1), "end": round(end + 0.4, 1), "factor": factor})

    def data(self) -> dict:
        return {"events": self.events, "speed_segments": self.speed}


def shot(pg, out: Path, name: str) -> None:
    try:
        pg.screenshot(path=str(out / f"{name}.png"))
    except Exception:
        pass


def wait_chat_idle(pg, timeout=130000) -> None:
    pg.wait_for_function("!document.querySelector('#chat [data-busy]')", timeout=timeout)
    pg.wait_for_timeout(700)


def login(pg, base: str, account: str, pin: str, path: str = "/m") -> None:
    pg.goto(base.rstrip("/") + path, wait_until="domcontentloaded")
    pg.wait_for_selector("#l_go", timeout=20000)
    pg.wait_for_timeout(700)
    acc = pg.locator(f".acc[data-k='{account}']")
    if acc.count():
        acc.first.click()
    else:
        pg.fill("#l_code", account)
        pg.fill("#l_pin", pin)
    pg.check("#l_consent")
    pg.click("#l_go")


def pick_vehicle(pg, plate: str) -> None:
    if pg.locator("#empty:not(.hidden) #vfind").count():
        box = pg.locator("#empty")
    else:
        pg.click("#vehBtn")
        pg.wait_for_selector("#layer #vfind", timeout=10000)
        box = pg.locator("#layer")
    box.locator("#vfind").fill(plate)
    try:
        pg.wait_for_selector("#btnDiag", timeout=1800)
    except PWTimeout:
        btn = box.locator(f"button[data-v='{plate}']")
        if btn.count():
            btn.first.click()
    pg.wait_for_selector("#sess", timeout=15000)
    pg.wait_for_timeout(600)


def choose_session(pg, session: str) -> None:
    pg.select_option("#sess", session)
    pg.wait_for_timeout(350)


def ask_typed(pg, text: str) -> float:
    start = time.monotonic()
    pg.click("#txt")
    pg.type("#txt", text, delay=28)
    pg.keyboard.press("Enter")
    return start


def wait_after_llm(pg, marks: Marks, name: str, started_mono: float, timeout=130000) -> None:
    start = started_mono - marks.t0
    wait_chat_idle(pg, timeout)
    marks.wait(name, start, marks.now())


def open_first_citation(pg, out: Path, name: str) -> None:
    cite = pg.locator("#chat button.cite").last
    if not cite.count():
        return
    cite.click()
    try:
        pg.wait_for_selector("#viewer:not(.hidden)", timeout=8000)
        pg.wait_for_timeout(2200)
        shot(pg, out, name)
        pg.click("#mClose")
        pg.wait_for_timeout(500)
    except PWTimeout:
        pass


def mechanic_part(pw, base: str, out: Path, smoke: bool) -> tuple[Path, dict]:
    rawdir = out / "mobile_raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    marks = Marks()
    browser = pw.chromium.launch(headless=True, executable_path=browser_executable(), args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 430, "height": 860}, record_video_dir=str(rawdir),
                              record_video_size={"width": 430, "height": 860}, device_scale_factor=1)
    pg = ctx.new_page()
    try:
        login(pg, base, "selftaught", "3002", "/m")
        pg.wait_for_selector("#app:not(.hidden)", timeout=20000)
        marks.event("login done")
        shot(pg, out, "01_login")

        pick_vehicle(pg, MAIN[0])
        choose_session(pg, MAIN[1])
        shot(pg, out, "02_vehicle")
        started = time.monotonic()
        pg.click("#btnDiag")
        pg.wait_for_selector(".verdict", timeout=20000)
        marks.event("verdict shown")
        wait_after_llm(pg, marks, "объяснение вердикта", started, 140000)
        marks.event("explanation done")
        shot(pg, out, "03_verdict_explain")
        open_first_citation(pg, out, "04_manual_page")

        if not smoke:
            started = ask_typed(pg, Q_VOICE)
            wait_after_llm(pg, marks, "ответ на вопрос", started, 130000)
            marks.event("voice-question answer done")
            shot(pg, out, "05_answer_contradiction")
            open_first_citation(pg, out, "06_citation_page")

            exp = pg.locator("button.act", has_text="Подробнее").first
            if exp.count():
                started = time.monotonic()
                exp.click()
                wait_after_llm(pg, marks, "подробный ответ", started, 130000)
                marks.event("expand done")
                shot(pg, out, "07_expand")

            started = ask_typed(pg, TICKET)
            try:
                pg.wait_for_selector("select.urg-pick", timeout=130000)
                wait_chat_idle(pg, 5000)
            except PWTimeout:
                pass
            marks.wait("черновик заявки", started - marks.t0, marks.now())
            draft = pg.locator(".msg", has=pg.locator("select.urg-pick")).last
            if draft.count():
                draft.locator("select.urg-pick").select_option("high")
                pg.wait_for_timeout(500)
                shot(pg, out, "08_ticket_draft")
                draft.locator(".card-actions .btn.primary").click()
                pg.wait_for_function("document.body.innerText.includes('TKT-')", timeout=20000)
                marks.event("ticket created")
                pg.wait_for_timeout(1200)
                shot(pg, out, "09_ticket_created")

            pick_vehicle(pg, LOWCONF[0])
            choose_session(pg, LOWCONF[1])
            started = time.monotonic()
            pg.click("#btnDiag")
            pg.wait_for_selector(".verdict", timeout=20000)
            wait_after_llm(pg, marks, "объяснение 0,48", started, 90000)
            shot(pg, out, "10_low_confidence")
            started = ask_typed(pg, TICKET)
            pg.wait_for_function("document.body.innerText.includes('Заявку не оформляю')", timeout=20000)
            marks.wait("отказ в заявке 0,48", started - marks.t0, marks.now())
            pg.wait_for_timeout(1000)
            shot(pg, out, "11_low_refusal")

            pick_vehicle(pg, MISMATCH[0])
            choose_session(pg, MISMATCH[1])
            pg.click("#btnDiag")
            pg.wait_for_selector(".verdict", timeout=20000)
            pg.wait_for_function("document.body.innerText.includes('ошибка модели')", timeout=10000)
            pg.wait_for_timeout(1200)
            marks.event("mismatch shown")
            shot(pg, out, "12_mismatch")

            pick_vehicle(pg, MAIN[0])
            started = ask_typed(pg, Q_NODATA)
            wait_after_llm(pg, marks, "вопрос вне руководства", started, 130000)
            marks.event("nodata done")
            shot(pg, out, "13_nodata")

            pick_vehicle(pg, RESUME)
            pg.wait_for_function("document.body.innerText.includes('Остановились') || document.body.innerText.includes('Ремонт продолжается')", timeout=20000)
            pg.wait_for_timeout(1600)
            marks.event("resume shown")
            shot(pg, out, "14_resume")
    finally:
        ctx.close()
        browser.close()
    files = sorted(rawdir.glob("*.webm"), key=os.path.getmtime)
    if not files:
        raise RuntimeError("mobile video was not written")
    target = out / "take_mobile.webm"
    shutil.move(str(files[-1]), target)
    shutil.rmtree(rawdir, ignore_errors=True)
    return target, marks.data()


def click_tab(pg, name: str, wait_selector: str) -> None:
    pg.click(f"#tabs button[data-tab='{name}']")
    pg.wait_for_selector(wait_selector, timeout=15000)
    pg.wait_for_timeout(1300)


def desktop_part(pw, base: str, out: Path, smoke: bool) -> tuple[Path, dict]:
    rawdir = out / "desktop_raw"
    rawdir.mkdir(parents=True, exist_ok=True)
    marks = Marks()
    browser = pw.chromium.launch(headless=True, executable_path=browser_executable(), args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(rawdir),
                              record_video_size={"width": 1280, "height": 720}, device_scale_factor=1)
    pg = ctx.new_page()
    try:
        login(pg, base, "chief", "1001", "/")
        pg.wait_for_selector("#kpis .kpi", timeout=20000)
        marks.event("manager fleet")
        pg.wait_for_timeout(1200)
        shot(pg, out, "20_manager_fleet")
        if not smoke:
            details = pg.locator("#fbar details")
            if details.count():
                details.first.evaluate("d => { d.open = true }")
                pg.wait_for_timeout(300)
                status = pg.locator("#f_status")
                if status.count():
                    status.select_option("quarantine")
                    pg.wait_for_timeout(1200)
                    shot(pg, out, "21_manager_quarantine")
            click_tab(pg, "tickets", "#tab-tickets .tbl, #tab-tickets .panel")
            shot(pg, out, "22_manager_tickets")
            click_tab(pg, "handoffs", "#tab-handoffs .tbl, #tab-handoffs .panel")
            shot(pg, out, "23_manager_handoffs")
            marks.event("manager tabs done")
        pg.evaluate("fetch('/api/logout',{method:'POST'})")
        pg.wait_for_timeout(700)

        login(pg, base, "engineer", "2002", "/")
        pg.wait_for_selector("#health .kpi, #health", timeout=20000)
        pg.wait_for_timeout(1200)
        marks.event("engineer health")
        shot(pg, out, "30_engineer_health")
        if not smoke:
            click_tab(pg, "companies", "#tab-companies .tbl")
            shot(pg, out, "31_engineer_companies")
            click_tab(pg, "cost", "#tab-cost .tbl")
            pg.wait_for_timeout(800)
            shot(pg, out, "32_engineer_cost")
            click_tab(pg, "logs", "#tab-logs .tbl")
            row = pg.locator("#tab-logs tr[data-id]").first
            if row.count():
                row.click()
                pg.wait_for_selector("#layer .side", timeout=8000)
                pg.wait_for_timeout(1500)
                shot(pg, out, "33_engineer_trace")
            marks.event("engineer tabs done")
        pg.goto(base.rstrip("/") + "/calculator", wait_until="domcontentloaded")
        pg.wait_for_selector("body", timeout=10000)
        pg.wait_for_timeout(1800)
        marks.event("calculator")
        shot(pg, out, "40_calculator")
    finally:
        ctx.close()
        browser.close()
    files = sorted(rawdir.glob("*.webm"), key=os.path.getmtime)
    if not files:
        raise RuntimeError("desktop video was not written")
    target = out / "take_desktop.webm"
    shutil.move(str(files[-1]), target)
    shutil.rmtree(rawdir, ignore_errors=True)
    return target, marks.data()


def seg_args(marks: dict) -> list[str]:
    out = []
    last = 0.0
    for s in sorted(marks.get("speed_segments") or [], key=lambda x: x["start"]):
        st, en = float(s["start"]), float(s["end"])
        if en <= st or st < last:
            continue
        out.append(f"{st:.1f}-{en:.1f}:{s['factor']}")
        last = en
    return out


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def speedup_one(inp: Path, outp: Path, marks: dict) -> Path:
    args = seg_args(marks)
    if not args:
        shutil.copyfile(inp, outp)
        return outp
    run(["bash", str(ROOT / "scripts/video_speedup.sh"), str(inp), str(outp), *args])
    return outp


def concat_fast(mobile: Path, desktop: Path, outp: Path) -> None:
    filt = ("[0:v]scale=-2:720,pad=1280:720:(ow-iw)/2:0:color=0x0e1013,fps=30,setsar=1[v0];"
            "[1:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x0e1013,fps=30,setsar=1[v1];"
            "[v0][v1]concat=n=2:v=1:a=0[v]")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mobile), "-i", str(desktop),
         "-filter_complex", filt, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(outp)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:18610")
    ap.add_argument("--out", default=str(ROOT / "runs/video"))
    ap.add_argument("--smoke", action="store_true", help="record only login, first diagnosis, manager and calculator")
    ap.add_argument("--no-postprocess", action="store_true")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with sync_playwright() as pw:
        mobile, mobile_marks = mechanic_part(pw, a.url, out, a.smoke)
        desktop, desktop_marks = desktop_part(pw, a.url, out, a.smoke)
    result = {"url": a.url, "smoke": a.smoke, "mobile": mobile_marks, "desktop": desktop_marks, "wall_sec": round(time.time() - t0, 1)}
    (out / "markers.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("mobile:", mobile)
    print("desktop:", desktop)
    print("markers:", out / "markers.json")
    if not a.no_postprocess:
        mf = speedup_one(mobile, out / "mobile_fast.mp4", mobile_marks)
        df = speedup_one(desktop, out / "desktop_fast.mp4", desktop_marks)
        final = out / "digital_mechanic_demo_draft.mp4"
        concat_fast(mf, df, final)
        print("draft:", final)
    print("done in", round(time.time() - t0, 1), "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
