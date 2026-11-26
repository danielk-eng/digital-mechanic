"""One-take demo recording (Playwright, real time, no cuts). Usage:
   python scripts/record_demo.py VEHICLE_ID ANOMALY_SESSION FAILED_SESSION "question text" [out_dir]
Run scripts/run_demo.sh first. Scenarios in order: (1) anomaly -> ticket, (2) question, (3) failed recording.
"""
import sys
from playwright.sync_api import sync_playwright

vid, anomaly, failed, question = sys.argv[1:5]
out = sys.argv[5] if len(sys.argv) > 5 else "runs/video"
W, H = 430, 900
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    c = b.new_context(viewport={"width": W, "height": H}, record_video_dir=out, record_video_size={"width": W, "height": H}, device_scale_factor=1)
    pg = c.new_page()
    pg.goto("http://127.0.0.1:8000")
    pg.wait_for_selector("#veh option[value]:not([value=''])", state="attached")
    pg.wait_for_timeout(1500)
    pg.select_option("#veh", vid); pg.wait_for_timeout(1500)
    # scenario 1
    pg.select_option("#chk", anomaly); pg.wait_for_timeout(800)
    pg.click("#get")
    pg.wait_for_selector("button[data-confirm]", timeout=150000)
    pg.wait_for_timeout(2500)
    pg.evaluate("document.getElementById('log').scrollTo({top:document.getElementById('log').scrollHeight-1200,behavior:'smooth'})"); pg.wait_for_timeout(4000)
    pg.evaluate("document.getElementById('log').scrollTo({top:document.getElementById('log').scrollHeight,behavior:'smooth'})"); pg.wait_for_timeout(2500)
    pg.click("button[data-confirm]")
    pg.wait_for_function("document.body.innerText.includes('TKT-')", timeout=30000); pg.wait_for_timeout(2500)
    # scenario 2
    pg.type("#t", question, delay=45); pg.keyboard.press("Enter")
    pg.wait_for_timeout(1000); pg.wait_for_function("![...document.querySelectorAll('.m.a')].some(e=>e.textContent.trim()==='…')", timeout=120000); pg.wait_for_timeout(5000)
    # scenario 3
    pg.select_option("#chk", failed); pg.wait_for_timeout(800)
    pg.click("#get")
    pg.wait_for_function("document.body.innerText.includes('Запись непригодна')", timeout=60000); pg.wait_for_timeout(4000)
    c.close(); b.close()
print("saved in", out)
