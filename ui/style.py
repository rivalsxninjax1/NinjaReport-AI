"""Shared dark theme. Applied once per page via apply_theme()."""
from __future__ import annotations

DARK_CSS = """
<style>
.stApp { background-color: #0d1117; color: #c9d1d9; }
h1, h2, h3 { color: #58a6ff; }
.stButton>button { background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
.stButton>button:hover { border-color: #58a6ff; color: #58a6ff; }
[data-testid="stSidebar"] { background-color: #161b22; }
.status-verified { color: #3fb950; font-weight: 600; }
.status-unverified { color: #d29922; font-weight: 600; }
.status-disputed { color: #f85149; font-weight: 600; }
.severity-critical { color: #f85149; font-weight: 700; }
.severity-high { color: #ff7b72; font-weight: 700; }
.severity-medium { color: #d29922; font-weight: 700; }
.severity-low { color: #58a6ff; font-weight: 700; }
.severity-informational { color: #8b949e; font-weight: 700; }
</style>
"""


def apply_theme() -> None:
    import streamlit as st
    st.markdown(DARK_CSS, unsafe_allow_html=True)


def status_badge(status: str) -> str:
    return f'<span class="status-{status}">{status.upper()}</span>'


def severity_badge(severity: str | None) -> str:
    if not severity:
        return '<span class="status-unverified">NOT YET APPROVED</span>'
    return f'<span class="severity-{severity}">{severity.upper()}</span>'
