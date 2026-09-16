"""Shared, testable wiring used by every page. Deliberately Streamlit-free
at the top so the store construction itself can be unit tested without a
Streamlit runtime — only get_stores()/sidebar helpers touch `st`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ai.cache import AnalysisCache
from ai.ollama_provider import OllamaProvider
from config import Settings, get_settings
from core.attack_path_store import AttackPathStore
from core.crop_store import CropStore
from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.graph_store import RelationshipStore
from core.report_store import ReportStore
from processors.derived_store import DerivedStore


@dataclass
class Stores:
    settings: Settings
    conn: sqlite3.Connection
    evidence_store: EvidenceStore
    derived_store: DerivedStore
    crop_store: CropStore
    relationship_store: RelationshipStore
    findings_store: FindingsStore
    attack_path_store: AttackPathStore
    report_store: ReportStore
    ai_cache: AnalysisCache
    ollama: OllamaProvider


def build_stores(settings: Settings | None = None) -> Stores:
    """Pure construction, no Streamlit dependency — directly unit-testable."""
    settings = settings or get_settings()
    settings.ensure_directories()
    conn = get_connection(settings.db_path())
    init_schema(conn)

    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")

    return Stores(
        settings=settings,
        conn=conn,
        evidence_store=evidence_store,
        derived_store=DerivedStore(conn),
        crop_store=CropStore(conn),
        relationship_store=RelationshipStore(conn),
        findings_store=FindingsStore(conn),
        attack_path_store=AttackPathStore(conn),
        report_store=ReportStore(conn),
        ai_cache=AnalysisCache(conn),
        ollama=OllamaProvider(
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.ai_timeout_seconds,
        ),
    )


def derived_root(stores: Stores) -> Path:
    return stores.settings.data_dir / "derived"


def get_stores():
    """Streamlit-cached accessor. Only this function touches `st` —
    everything else in this module is plain, testable Python."""
    import streamlit as st

    @st.cache_resource
    def _cached() -> Stores:
        return build_stores()

    return _cached()


def project_selector(stores: Stores):
    """Sidebar project picker + inline create. Returns the selected Project
    or None if no projects exist yet. Shared across every page."""
    import streamlit as st

    projects = stores.evidence_store.list_projects()
    st.sidebar.subheader("Project")

    if projects:
        names = {p.name: p for p in projects}
        choice = st.sidebar.selectbox("Active project", list(names.keys()))
        selected = names[choice]
    else:
        st.sidebar.info("No projects yet — create one below.")
        selected = None

    with st.sidebar.expander("+ New project"):
        new_name = st.text_input("Project name", key="new_project_name")
        if st.button("Create project", key="create_project_button") and new_name.strip():
            stores.evidence_store.create_project(new_name.strip())
            st.rerun()

    return selected


def save_uploaded_bytes(data: bytes, suggested_name: str, tmp_dir: Path) -> Path:
    """Write uploaded bytes to a scratch path so EvidenceStore.add_evidence
    (which takes a Path) can validate/hash/copy it. Kept Streamlit-free and
    directly unit-testable — the caller passes st.file_uploader's .getvalue()
    and .name, not the UploadedFile object itself."""
    import uuid

    from core.safe_files import sanitize_filename

    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / f"upload_{uuid.uuid4().hex}_{sanitize_filename(suggested_name)}"
    dest.write_bytes(data)
    return dest
