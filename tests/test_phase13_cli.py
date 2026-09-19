"""Phase 13 acceptance tests: CLI.

Tests call the cmd_* functions directly against real stores (no
Streamlit needed — the CLI never imports it), plus a couple of
subprocess-level checks for `python -m cli` dispatch itself.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cli.main import cmd_analyze, cmd_doctor, cmd_export, cmd_generate, cmd_projects, main
from config import Settings
from ui.common import build_stores


@pytest.fixture
def stores(tmp_path):
    settings = Settings(data_dir=tmp_path / "appdata", export_dir=tmp_path / "appdata" / "exports")
    s = build_stores(settings)
    yield s
    s.conn.close()


def test_cmd_projects_reports_empty(stores, capsys):
    code = cmd_projects(stores)
    assert code == 0
    assert "No projects yet" in capsys.readouterr().out


def test_cmd_doctor_runs_and_shows_paths(stores, capsys):
    code = cmd_doctor(stores)
    assert code == 0
    assert "Data directory" in capsys.readouterr().out


def test_cmd_analyze_auto_creates_project_and_ingests(stores, tmp_path, capsys):
    pytest.importorskip("PIL")
    from PIL import Image

    img_path = tmp_path / "shot.png"
    Image.new("RGB", (400, 300), "white").save(img_path)

    code = cmd_analyze(stores, img_path, None)
    out = capsys.readouterr().out
    assert code == 0
    assert "Using project" in out
    assert "Ingested as EVD-001" in out


def test_cmd_analyze_missing_file_returns_error(stores, tmp_path):
    code = cmd_analyze(stores, tmp_path / "does_not_exist.png", None)
    assert code == 1


def test_cmd_analyze_unknown_explicit_project_returns_error(stores, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    img_path = tmp_path / "shot.png"
    Image.new("RGB", (100, 100), "white").save(img_path)
    code = cmd_analyze(stores, img_path, "PRJ-doesnotexist")
    assert code == 1


def test_cmd_generate_fails_without_report_plan(stores, capsys):
    project = stores.evidence_store.create_project("No Plan Yet")
    code = cmd_generate(stores, project.id)
    assert code == 1


def test_cmd_generate_succeeds_with_report_plan(stores, tmp_path, capsys):
    pytest.importorskip("PIL")
    from PIL import Image

    project = stores.evidence_store.create_project("Has Plan")
    img_path = tmp_path / "shot.png"
    Image.new("RGB", (100, 100), "white").save(img_path)
    evidence = stores.evidence_store.add_evidence(
        project_id=project.id, source_path=img_path, original_filename="shot.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    plan = stores.report_store.create_plan(project.id, "ctf")
    stores.report_store.add_evidence_item(project.id, plan.sections[0].id, evidence.id, evidence_store=stores.evidence_store)

    code = cmd_generate(stores, project.id)
    out = capsys.readouterr().out
    assert code == 0
    assert "Generated:" in out


def test_cmd_export_creates_archive(stores, tmp_path):
    project = stores.evidence_store.create_project("Export Me")
    output_path = tmp_path / "out.nra"
    code = cmd_export(stores, project.id, output_path)
    assert code == 0
    assert output_path.exists()


def test_cmd_export_unknown_project_returns_error(stores):
    code = cmd_export(stores, "PRJ-nope", None)
    assert code == 1


# ---------- argparse dispatch ----------

def test_main_dispatches_projects_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NINJAREPORT_DATA_DIR", str(tmp_path / "appdata"))
    code = main(["projects"])
    assert code == 0
    assert "No projects yet" in capsys.readouterr().out


def test_python_dash_m_cli_help_lists_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "cli", "--help"],
        cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "doctor" in result.stdout
    assert "analyze" in result.stdout


def test_python_dash_m_cli_with_no_command_exits_nonzero():
    result = subprocess.run(
        [sys.executable, "-m", "cli"],
        cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
    )
    assert result.returncode != 0
