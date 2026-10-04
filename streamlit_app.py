"""Community Cloud entry point using exactly the FastAPI frontend assets."""
from pathlib import Path
import os

import streamlit as st
import streamlit.components.v1 as components

from streamlit_transport import BrowserSession
from streamlit_frontend import frontend_html

BASE_DIR = Path(__file__).resolve().parent
st.set_page_config(page_title='PaperRef Finder', page_icon='📚', layout='wide', initial_sidebar_state='collapsed')

for name in ('PAPERREF_CONTACT_EMAIL', 'OPENALEX_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY', 'CORE_API_KEY', 'SERPAPI_API_KEY'):
    try:
        value = st.secrets[name]
        if isinstance(value, str) and value:
            os.environ.setdefault(name, value)
    except (KeyError, FileNotFoundError):
        pass

st.markdown('''<style>
[data-testid="stHeader"], [data-testid="stToolbar"], footer {display:none!important}
.stMainBlockContainer {padding:0!important;max-width:none!important}
[data-testid="stMain"] {overflow:hidden!important}
[data-testid="stVerticalBlock"] {gap:0!important}
iframe {display:block;border:0;width:100%}
</style>''', unsafe_allow_html=True)


workspace = components.declare_component('paperref_workspace', path=str(BASE_DIR / 'streamlit_component'))
if 'paperref_transport' not in st.session_state:
    st.session_state.paperref_transport = BrowserSession()


@st.fragment(run_every=0.5)
def render_workspace():
    session = st.session_state.paperref_transport
    value = workspace(html=frontend_html() if session.needs_page else None,
                      **session.snapshot(), key='paperref-workspace', default=None)
    session.accept(value)


render_workspace()
