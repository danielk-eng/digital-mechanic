"""UI screenshots for review: python scripts/shots.py [base] [outdir]"""
import sys
from playwright.sync_api import sync_playwright

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
OUT = sys.argv[2] if len(sys.argv) > 2 else "runs/shots"
import os; os.makedirs(OUT, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    c = b.new_context(viewport={"width": 400, "height": 860})
    pg = c.new_page()
    pg.goto(B); pg.wait_for_selector("#l_go")
    pg.screenshot(path=f"{OUT}/01_login.png")
    pg.fill("#l_name", "Сергей"); pg.check("#l_consent"); pg.click("#l_go")
    pg.wait_for_selector("#vehicle option[value='M333YK47']", state="attached")
    pg.select_option("#vehicle", "M333YK47"); pg.wait_for_timeout(800)
    pg.select_option("#sess", "S-0005"); pg.click("#btnDiag")
    pg.wait_for_selector(".verdict"); pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/02_verdict.png")
    pg.wait_for_timeout(12000); pg.screenshot(path=f"{OUT}/03_progress.png")
    pg.wait_for_selector(".msg.bot .b:not(.card) >> text=/./", timeout=200000)
    pg.wait_for_function("!document.querySelector('.thinking')", timeout=200000)
    pg.screenshot(path=f"{OUT}/04_explain.png", full_page=True)
    m = c.new_page(); 
    c2 = b.new_context(viewport={"width": 1280, "height": 900}); mp = c2.new_page()
    mp.goto(B); mp.wait_for_selector("#l_go"); mp.fill("#l_name", "Главный"); mp.check("input[value=manager]"); mp.check("#l_consent"); mp.click("#l_go")
    mp.wait_for_selector(".tile"); mp.wait_for_timeout(800); mp.screenshot(path=f"{OUT}/05_manager.png", full_page=False)
    mp.click("tr[data-v]"); mp.wait_for_selector("#drawer:not(.hidden)"); mp.wait_for_timeout(500); mp.screenshot(path=f"{OUT}/06_vehicle.png")
    b.close()
