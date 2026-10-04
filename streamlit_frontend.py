"""Package the same frontend assets for a Streamlit component, without a UI fork."""
from pathlib import Path
import base64
import html as html_module
from web_security import content_policy

BASE_DIR = Path(__file__).resolve().parent


def frontend_html():
    html = (BASE_DIR / 'static' / 'index.html').read_text()
    for name in ('utilities.css', 'app.css'):
        css = (BASE_DIR / 'static' / name).read_text()
        html = html.replace('<link rel="stylesheet" href="/static/' + name + '">', '<style>' + css + '</style>')
    for name in ('progress.js', 'settings.js', 'workspace.js', 'history.js', 'graph.js', 'vendor/pdf.min.js', 'pdf-preview.js'):
        script = (BASE_DIR / 'static' / name).read_text()
        html = html.replace('<script src="/static/' + name + '"></script>', '<script>' + script + '</script>')
    # The PDF worker is an inert asset, not a user PDF. Avoid a second network
    # route or a module-script fork inside Streamlit's shared component.
    worker = base64.b64encode((BASE_DIR / 'static/vendor/pdf.worker.min.js').read_bytes()).decode('ascii')
    html = html.replace('</body>', '<template id="pdf-worker-data">' + worker + '</template></body>', 1)
    policy = html_module.escape(content_policy(html), quote=True)
    html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="' + policy + '"><meta name="referrer" content="no-referrer">', 1)
    return html.replace('class="brand" href="/"', 'class="brand" href="#paper-input"')
