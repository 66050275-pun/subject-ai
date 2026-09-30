"""Package the same frontend assets for a Streamlit component, without a UI fork."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def frontend_html():
    html = (BASE_DIR / 'static' / 'index.html').read_text()
    css = (BASE_DIR / 'static' / 'app.css').read_text()
    graph = (BASE_DIR / 'static' / 'graph.js').read_text()
    html = html.replace('<link rel="stylesheet" href="/static/app.css">', '<style>' + css + '</style>')
    html = html.replace('<script src="/static/graph.js"></script>', '<script>' + graph + '</script>')
    return html.replace('class="brand" href="/"', 'class="brand" href="#paper-input"')
