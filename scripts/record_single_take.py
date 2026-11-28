"""Record the demo in ONE continuous fragment: a real browser on a virtual display plus live narration.

  python scripts/record_single_take.py --url http://127.0.0.1:18610 --out runs/take1
  python scripts/record_single_take.py --smoke     # short pipeline check: login + diagnosis only

The whole scenario is captured by a single ffmpeg process (x11grab of Xvfb :99 + PipeWire monitor) into one MP4.
No cutting, no concatenation, no post effects: the TTS narration is played through the speakers while recording,
so the soundtrack is captured in the same take. The browser starts narrow (the mechanic mobile shell, like a
phone) and is resized wide for the manager/engineer part — a natural continuous transition in one window.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_demo import (MAIN, TICKET, ask_dictated, ask_typed, browser_executable,
                         choose_session, login, pick_vehicle, shot, voice_loop_wav, wait_chat_idle)

ROOT = Path(__file__).resolve().parents[1]
DISPLAY = ":99"
SCREEN = "1280x800"
MONITOR = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"

NARRATION = {
    "intro": "Цифровой механик. Механик входит по PIN-коду, выбирает машину и запись проверки НейроДВС.",
    "explain": "Модель видит отклонение. Помощник объясняет вердикт простыми словами, и каждая цифра — со ссылкой на страницу руководства.",
    "cite": "Страница первоисточника открывается прямо в приложении. Любую цифру можно проверить одним нажатием.",
    "voice": "Вопрос можно продиктовать голосом. Если руководства расходятся, помощник честно показывает оба значения и безопасное правило, а не выбирает скрыто.",
    "ticket": "Заявка собирается автоматически: работы, запчасти, срочность. Механик только проверяет и подтверждает.",
    "manager": "Главный специалист видит весь парк: машины на карантине, заявки, повторы неисправностей.",
    "handoff": "Если ответа нет в руководствах, помощник не выдумывает, а передаёт вопрос главному специалисту.",
    "engineer": "Инженер видит журнал агента: каждый вызов модели, инструменты и стоимость ответа в токенах.",
    "calc": "Калькулятор считает экономику решения по реальным замерам токенов на живом стенде.",
}


def ffprobe_duration(path: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], text=True).strip())


def synthesize_all(base: str, out: Path) -> dict:
    """One wav per cue through the stand's server TTS (the same voice the product uses)."""
    c = httpx.Client(base_url=base.rstrip("/"), timeout=90, follow_redirects=True)
    c.post("/api/login/demo", json={"account": "selftaught", "pin": "3002", "consent": True}).raise_for_status()
    wavs = {}
    ndir = out / "narration"
    ndir.mkdir(parents=True, exist_ok=True)
    for key, text in NARRATION.items():
        wav = ndir / f"{key}.wav"
        if not wav.exists():
            r = c.post("/api/tts", json={"text": text, "lang": "ru"})
            r.raise_for_status()
            wav.write_bytes(r.content)
        wavs[key] = wav
    return wavs


class Player:
    """Serialised live playback: the next cue never overlaps the previous one."""

    def __init__(self):
        self.proc = None

    def say(self, wav: Path) -> None:
        self.wait()
        self.proc = subprocess.Popen(["pw-play", str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self) -> None:
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


class Recorder:
    """Single continuous ffmpeg capture: screen + monitor audio in one process, one output file."""

    def __init__(self, out: Path):
        self.out = out
        self.proc = None

    def start(self) -> None:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "x11grab", "-video_size", SCREEN, "-framerate", "25", "-i", DISPLAY,
               "-f", "pulse", "-i", MONITOR,
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(self.out)]
        self.proc = subprocess.Popen(cmd, env={**os.environ, "DISPLAY": DISPLAY})
        time.sleep(1.5)
        if self.proc.poll() is not None:
            raise RuntimeError("ffmpeg recorder did not start")

    def stop(self) -> None:
        if not self.proc:
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None


def xdotool(*args: str) -> str:
    r = subprocess.run(["xdotool", *args], env={**os.environ, "DISPLAY": DISPLAY}, capture_output=True, text=True)
    return r.stdout.strip()


def browser_window() -> str | None:
    for cls in ("chromium", "Chromium", "chrome"):
        ids = xdotool("search", "--onlyvisible", "--class", cls).split()
        if ids:
            return ids[-1]
    return None


def size_window(cdp, w: int, h: int, x: int, y: int) -> None:
    """Resize the real browser window through DevTools: layout follows because the context uses no_viewport."""
    win = cdp.send("Browser.getWindowForTarget")
    cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
    cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"left": x, "top": y, "width": w, "height": h}})


def run_scenario(base: str, out: Path, voice: Path, wavs: dict, player: Player, event, smoke: bool) -> None:
    with sync_playwright() as pw:
        # Playwright's own chromium, not the system snap: the snap's private /tmp cannot reach the Xvfb socket.
        browser = pw.chromium.launch(
            headless=False,
            env={**os.environ, "DISPLAY": DISPLAY, "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"},
            args=["--no-sandbox", "--ozone-platform=x11", "--window-size=450,780", "--window-position=415,8",
                  "--force-device-scale-factor=1", "--disable-session-crashed-bubble", "--no-first-run",
                  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                  f"--use-file-for-fake-audio-capture={voice.resolve()}"])
        ctx = browser.new_context(no_viewport=True, accept_downloads=False)
        pg = ctx.new_page()
        try:
            time.sleep(2.5)  # let the window appear on the virtual screen
            cdp = ctx.new_cdp_session(pg)
            size_window(cdp, 500, 780, 390, 8)

            # --- mechanic, mobile shell ---
            player.say(wavs["intro"])
            login(pg, base, "selftaught", "3002", "/m")
            pg.wait_for_selector("#app:not(.hidden)", timeout=20000)
            event("login mechanic")
            pick_vehicle(pg, MAIN[0])
            choose_session(pg, MAIN[1])
            event("vehicle picked")
            player.wait()
            pg.click("#btnDiag")
            pg.wait_for_selector(".verdict", timeout=20000)
            player.say(wavs["explain"])
            wait_chat_idle(pg, 140000)
            event("verdict explained")
            shot(pg, out, "s01_verdict")
            player.wait()

            cite = pg.locator("#chat button.cite").last
            if cite.count():
                player.say(wavs["cite"])
                cite.click()
                try:
                    pg.wait_for_selector("#viewer:not(.hidden)", timeout=8000)
                    pg.wait_for_timeout(2800)
                    shot(pg, out, "s02_cite")
                    pg.click("#mClose")
                    pg.wait_for_timeout(600)
                except PWTimeout:
                    pass
                event("citation shown")
            if smoke:
                event("smoke done")
                return

            player.say(wavs["voice"])
            ask_dictated(pg)
            event("voice question asked")
            wait_chat_idle(pg, 130000)
            event("voice answer done")
            shot(pg, out, "s03_voice_answer")
            player.wait()

            player.say(wavs["ticket"])
            ask_typed(pg, TICKET)
            try:
                pg.wait_for_selector("select.urg-pick", timeout=130000)
                wait_chat_idle(pg, 8000)
            except PWTimeout:
                pass
            draft = pg.locator(".msg", has=pg.locator("select.urg-pick")).last
            if draft.count():
                draft.locator("select.urg-pick").select_option("high")
                pg.wait_for_timeout(700)
                draft.locator(".card-actions .btn.primary").click()
                pg.wait_for_function("document.body.innerText.includes('TKT-')", timeout=20000)
                event("ticket created")
                shot(pg, out, "s04_ticket")
                pg.wait_for_timeout(1500)
            player.wait()

            # --- resize wide: desktop part, same continuous take ---
            size_window(cdp, 1268, 790, 6, 4)
            pg.wait_for_timeout(1200)
            event("window wide")

            pg.evaluate("fetch('/api/logout',{method:'POST'})")
            pg.wait_for_timeout(800)
            player.say(wavs["manager"])
            login(pg, base, "chief", "1001", "/")
            pg.wait_for_selector("#kpis .kpi", timeout=20000)
            pg.wait_for_timeout(2000)
            event("manager fleet")
            shot(pg, out, "s05_fleet")

            details = pg.locator("#fbar details")
            if details.count():
                details.first.evaluate("d => { d.open = true }")
                pg.wait_for_timeout(300)
                status = pg.locator("#f_status")
                if status.count():
                    status.select_option("quarantine")
                    pg.wait_for_timeout(1500)
                    shot(pg, out, "s06_quarantine")
                    event("quarantine filter")
            for tab, sel in (("tickets", "#tab-tickets .tbl, #tab-tickets .panel"),
                             ("handoffs", "#tab-handoffs .tbl, #tab-handoffs .panel")):
                pg.click(f"#tabs button[data-tab='{tab}']")
                pg.wait_for_selector(sel, timeout=15000)
                pg.wait_for_timeout(1700)
                shot(pg, out, f"s07_{tab}")
                if tab == "handoffs":
                    player.say(wavs["handoff"])
                    pg.wait_for_timeout(2500)
            event("manager tabs done")
            player.wait()

            pg.evaluate("fetch('/api/logout',{method:'POST'})")
            pg.wait_for_timeout(800)
            player.say(wavs["engineer"])
            login(pg, base, "engineer", "2002", "/")
            pg.wait_for_selector("#health", timeout=20000)
            pg.wait_for_timeout(1600)
            event("engineer health")
            shot(pg, out, "s08_health")
            pg.click("#tabs button[data-tab='cost']")
            pg.wait_for_selector("#tab-cost .tbl", timeout=15000)
            pg.wait_for_timeout(1500)
            shot(pg, out, "s09_cost")
            pg.click("#tabs button[data-tab='logs']")
            pg.wait_for_selector("#tab-logs .tbl", timeout=15000)
            row = pg.locator("#tab-logs tr[data-id]").first
            if row.count():
                row.click()
                try:
                    pg.wait_for_selector("#layer .side", timeout=8000)
                    pg.wait_for_timeout(2000)
                    shot(pg, out, "s10_trace")
                except PWTimeout:
                    pass
            event("engineer done")
            player.wait()

            player.say(wavs["calc"])
            pg.goto(base + "/calculator", wait_until="domcontentloaded")
            pg.wait_for_selector("body", timeout=10000)
            pg.wait_for_timeout(3000)
            event("calculator")
            shot(pg, out, "s11_calc")
            player.wait()
            pg.wait_for_timeout(1200)
        finally:
            ctx.close()
            browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:18610")
    ap.add_argument("--out", default=str(ROOT / "runs/take1"))
    ap.add_argument("--smoke", action="store_true", help="short pipeline check: login + diagnosis only")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = a.url.rstrip("/")

    wavs = synthesize_all(base, out)
    voice = voice_loop_wav(base, out)
    player = Player()
    raw = out / "single_take.mp4"
    rec = Recorder(raw)
    marks = {"events": []}
    t0 = time.monotonic()

    def event(name: str) -> None:
        marks["events"].append({"t": round(time.monotonic() - t0, 1), "event": name})
        print(f"  [{round(time.monotonic() - t0, 1):6.1f}s] {name}", flush=True)

    rec.start()
    t0 = time.monotonic()
    try:
        run_scenario(base, out, voice, wavs, player, event, a.smoke)
    except Exception as e:
        print(f"scenario error: {e}", file=sys.stderr)
    player.wait()
    time.sleep(1.0)
    rec.stop()
    dur = ffprobe_duration(raw) if raw.exists() else 0
    marks["wall_sec"] = round(time.monotonic() - t0, 1)
    marks["video_sec"] = round(dur, 1)
    (out / "markers.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"single take: {raw} ({dur:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
