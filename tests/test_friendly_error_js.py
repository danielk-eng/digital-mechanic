"""A Cloudflare tunnel error page (HTML, 530) or a dropped connection was shown in the chat as raw markup (audit 27.11 10:20):
friendlyError turns both into the generic «request failed» line, server messages stay as they are (run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "src/digital_mechanic/web/static/app.js"


def _run(msg: str, status: int | None = None) -> str:
    src = JS.read_text(encoding="utf-8")
    fn = src[src.index("function friendlyError"):src.index("function renderTurn")]
    e = f"Object.assign(new Error({json.dumps(msg, ensure_ascii=False)}), {{status: {json.dumps(status)}}})"
    return subprocess.run(["node", "-e", "const t = k => '<' + k + '>';" + fn + f"; process.stdout.write(friendlyError({e}))"],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_html_error_page_is_not_shown():
    page = '<!DOCTYPE html> <html class="no-js" lang="en-US"> <head> <title>Cloudflare Tunnel error | x.trycloudflare.com'
    assert _run(page, 530) == "<err_generic>"
    assert _run("<html><body>502 Bad Gateway</body></html>", 502) == "<err_generic>"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_network_failure_is_generic():
    assert _run("Failed to fetch") == "<err_generic>"
    assert _run("NetworkError when attempting to fetch resource.") == "<err_generic>"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_server_messages_kept():
    assert _run("Сначала выберите машину", 400) == "Сначала выберите машину"
    assert _run("x", 401) == "<err_session>"
    assert _run("ML-сервис недоступен", 502) == "<err_ml>"
