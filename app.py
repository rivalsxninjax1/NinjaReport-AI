"""NinjaReport AI — Streamlit entry point.

Phase 0: boots the app, loads config, sets up logging and data directories.
No evidence handling or AI logic yet — that's Phase 1+.
"""
from __future__ import annotations

import sys

from config import get_settings
from core.logging_setup import configure_logging


def bootstrap():
    """Load settings, ensure directories exist, configure logging.

    Returns the loaded Settings object. Kept dependency-free (no streamlit
    import) so it can be unit tested without a UI runtime.
    """
    settings = get_settings()
    settings.ensure_directories()
    logger = configure_logging(settings.data_dir)
    logger.info("NinjaReport AI starting up. data_dir=%s", settings.data_dir)
    return settings


def main() -> None:
    settings = bootstrap()

    try:
        import streamlit as st
    except ImportError:
        print(
            "Streamlit is not installed. Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    st.set_page_config(page_title="NinjaReport AI", layout="wide")
    st.title("NinjaReport AI")
    st.caption("Local-first CTF/VAPT evidence-to-report generator")

    st.success("Phase 0 skeleton is running.")
    st.write(f"Data directory: `{settings.data_dir}`")
    st.write(f"Export directory: `{settings.export_dir}`")
    st.info(
        "No projects, evidence, or AI features yet — those arrive in "
        "Phase 1 (evidence core) and Phase 3 (Ollama integration)."
    )


if __name__ == "__main__":
    main()
