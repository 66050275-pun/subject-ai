"""Package the same frontend assets for a Streamlit component, without a UI fork."""
from pathlib import Path
import html as html_module
from web_security import content_policy

BASE_DIR = Path(__file__).resolve().parent


def frontend_html():
    html = (BASE_DIR / 'static' / 'index.html').read_text()
    for name in ('utilities.css', 'app.css'):
        css = (BASE_DIR / 'static' / name).read_text()
        html = html.replace('<link rel="stylesheet" href="/static/' + name + '">', '<style>' + css + '</style>')
    for name in ('progress.js', 'settings.js', 'graph.js'):
        script = (BASE_DIR / 'static' / name).read_text()
        html = html.replace('<script src="/static/' + name + '"></script>', '<script>' + script + '</script>')
    policy = html_module.escape(content_policy(html), quote=True)
    html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="' + policy + '"><meta name="referrer" content="no-referrer">', 1)
    return html.replace('class="brand" href="/"', 'class="brand" href="#paper-input"')
