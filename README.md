# NinjaReport AI

Local-first AI-powered CTF/VAPT evidence-to-report generator. Runs entirely
on your machine — Streamlit UI, SQLite storage, local filesystem, OCR, and
Ollama for local AI. No cloud services, no telemetry.

> **Status:** Phase 0 — repository foundation. No evidence handling or AI
> features yet. See the phase roadmap below.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally (for Phase 3+)
- Tesseract OCR installed on your system (for Phase 2+, optional)

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m pytest
streamlit run app.py
```

## Project layout

```
ninjareport-ai/
├── app.py            # Streamlit entry point
├── config/           # Environment-driven settings
├── core/             # Logging, shared utilities
├── ai/               # AI provider abstraction (Phase 3+)
├── processors/       # File/OCR processing (Phase 2+)
├── findings/         # Findings & attack path (Phase 6+)
├── generators/        # DOCX/PDF report generation (Phase 8+)
├── ui/               # Streamlit page modules (Phase 9+)
├── templates/        # Report templates
├── data/             # Local app data (gitignored)
└── tests/            # pytest suite
```

## Roadmap

0. Repository foundation ✅ (this phase)
1. Project + evidence core
2. File processing + OCR
3. Ollama local AI
4. Evidence intelligence
5. Smart screenshots + evidence graph
6. Findings + attack path
7. Report builder
8. DOCX + PDF generation
9. Streamlit UX
10. Security + quality hardening
11. Performance for constrained hardware
12. Project archives (.nra)
13. CLI + packaging
14. Release + documentation

## Privacy model

All evidence, analysis, and reports stay on disk under your configured data
directory. No data leaves your machine except local calls to Ollama at
`OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`). Nothing is scanned,
attacked, or executed automatically — this tool documents evidence you
provide; it does not act as an autonomous offensive agent.

## Development

```bash
make install   # install dependencies
make test      # run test suite
make lint      # ruff check
make format    # ruff format
make run       # launch the Streamlit app
```

## License

MIT — see [LICENSE](LICENSE).
