# NinjaReport AI

A local-first report writer for CTF and VAPT work. Paste command output, drop in
screenshots and PDFs, pick a report type and format, and get a structured report:
evidence appendix, AI-drafted findings, and written narrative sections.

Everything runs on your own machine. The only network call the app makes is to
your local [Ollama](https://ollama.com) server.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set OLLAMA_TEXT_MODEL in .env
streamlit run app.py
```

Optional but recommended:

| Tool | Why | Install |
|---|---|---|
| Ollama | AI analysis, findings, narrative | https://ollama.com, then `ollama pull <model>` |
| Tesseract | OCR for screenshots and scanned PDFs | `brew install tesseract` / `sudo apt install tesseract-ocr` |

Without Ollama the app still works: evidence is stored and OCR'd, and reports are
generated with the evidence appendix but without AI-drafted findings or narrative.

Check your setup any time with `python -m cli doctor`, or the **Settings** page.

## Using it

**Quick Report (home page).** Paste text and/or drop files, choose CTF or VAPT and
PDF / DOCX / Markdown, click **Generate Report**. Review the result before sharing it.

**Step-by-step pages** (sidebar) for reviewing and refining:

| Page | What it's for |
|---|---|
| Projects | List and rename projects |
| Evidence | Upload, search, review, reprocess OCR, delete |
| Analysis | Run AI analysis on one item or all of them |
| Findings | Create, edit status, approve severity, delete |
| Attack Path | Evidence-backed kill-chain nodes |
| Report Builder | Reorder sections, include/exclude items, add captions |
| Exports | Generate DOCX and PDF from the report plan |
| Settings | Diagnostics for Ollama, OCR, and data locations |
| Archive | Export/import projects, back up and restore the database, delete a project safely |

## Command line

```bash
python -m cli doctor                      # check Ollama, OCR, PDF, data paths
python -m cli projects                    # list projects
python -m cli analyze path/to/file.png    # ingest (and analyze) one file
python -m cli generate PRJ-xxxxxxxx       # DOCX + PDF from a project's report plan
python -m cli export PRJ-xxxxxxxx out.nra # portable archive
python scripts/launch_desktop.py          # start the app and open your browser
```

## Where your data lives

| OS | Default location |
|---|---|
| macOS | `~/Library/Application Support/NinjaReportAI` |
| Linux | `~/.local/share/NinjaReportAI` |
| Windows | `%APPDATA%\NinjaReportAI` |

Override with `NINJAREPORT_DATA_DIR` in `.env`. Inside: `db/` (SQLite), `evidence/`,
`derived/` (previews and OCR output), `exports/` (generated reports), `archives/`
(`.nra` files), `backups/` (database snapshots), `logs/`.

## Privacy and safety model

- **Local only.** The server binds to `127.0.0.1` (see `.streamlit/config.toml`), usage
  statistics are off, and nothing is uploaded anywhere.
- **Evidence is immutable.** Originals are stored read-only with a SHA-256 hash, and the
  hash is re-checked before a file goes into a report.
- **AI never approves its own work.** AI output is schema-validated, always starts
  *unverified*, and AI-suggested severities stay *not yet approved* until you approve them.
- **Uploaded files are never executed.** Executable and script extensions are rejected,
  filenames are sanitized, and archive imports are checked for path traversal.
- **Models are never downloaded for you.** If a model is missing, the app tells you the
  `ollama pull` command to run.

## Supported files

Upload: PNG, JPG/JPEG, PDF, TXT (up to `MAX_UPLOAD_MB`, default 50).
Output: PDF, Word (.docx), Markdown (.md).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Ollama is offline" | Start it (`ollama serve` or open the app), then retry. |
| "Text model isn't ready" | `ollama pull <model>` and set `OLLAMA_TEXT_MODEL` in `.env` to the exact name shown by `ollama list`. |
| Settings page says Tesseract not found | Install it (see table above); screenshots still upload without it, just with no OCR text. |
| Word shows an empty Table of Contents | Press F9, or right-click it and choose *Update Field*. |
| After restoring a backup the data looks old | Restart the app; restore replaces the database file underneath a running app. |
| "Database is locked" | Rare; the app waits 5 seconds before failing. Close duplicate tabs or other running copies. |
| `.env` changes have no effect | Restart `streamlit run app.py`. |

## Known limitations

- `OLLAMA_VISION_MODEL`, `OCR_ENABLED`, and `PDF_EXPORT_ENABLED` are read but not acted on yet;
  screenshots are analyzed through their OCR text, not by a vision model.
- The PDF table of contents lists sections but isn't linked to page numbers.
- Database backups contain the database only; use project **Export** for a self-contained copy.
- Plain-text evidence is shown in reports up to 4,000 characters.
- Single user, single machine.

## Development

```bash
python -m pytest -q
ruff check .
python scripts/benchmark_pipeline.py   # OCR/preview timing
python scripts/ollama_doctor.py        # live Ollama check
```

CI (`.github/workflows/ci.yml`) runs the test suite on Python 3.11–3.13; lint runs but
is advisory for now.

Layout: `app.py` + `pages/` (UI), `ui/` (shared UI helpers), `core/` (storage, search,
archives), `ai/` (Ollama provider, analysis, synthesis), `processors/` (OCR, previews),
`generators/` (DOCX/PDF/Markdown), `cli/`, `scripts/`, `tests/`.

## License

MIT — see [LICENSE](LICENSE).
