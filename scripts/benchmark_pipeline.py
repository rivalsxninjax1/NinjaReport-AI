#!/usr/bin/env python3
"""Before/after performance benchmark for the evidence processing pipeline.

Measures wall-clock latency and peak memory (via tracemalloc) for:
  1. First-time processing of N synthetic screenshots (baseline cost).
  2. Reprocessing the SAME evidence WITHOUT the idempotency guard
     (simulating pre-Phase-11 behavior: OCR/preview always re-run).
  3. Reprocessing the SAME evidence WITH the idempotency guard
     (Phase 11 behavior: skip already-processed evidence).

This is a real, runnable measurement — not an estimate. Run it yourself:
    python scripts/benchmark_pipeline.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from processors.derived_store import DerivedStore
from processors.ocr_engine import run_ocr_on_image
from processors.image_processor import generate_preview
from processors.pipeline import process_evidence

N_IMAGES = 8
IMAGE_SIZE = (1600, 1200)  # a realistic full-resolution screenshot


def _make_synthetic_evidence(evidence_store: EvidenceStore, project_id: str, tmp_path: Path, n: int):
    from PIL import Image, ImageDraw

    evidence_list = []
    for i in range(n):
        img = Image.new("RGB", IMAGE_SIZE, "white")
        draw = ImageDraw.Draw(img)
        draw.text((50, 50), f"Terminal output line {i}: nmap -sV 10.0.0.{i} port 22 open SSH", fill="black")
        src = tmp_path / f"shot_{i}.png"
        img.save(src)
        evidence = evidence_store.add_evidence(
            project_id=project_id, source_path=src, original_filename=f"shot_{i}.png",
            evidence_type="screenshot", max_upload_mb=50,
        )
        evidence_list.append(evidence)
    return evidence_list


def _measure(label: str, fn) -> tuple[float, int]:
    tracemalloc.start()
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{label}: {elapsed:.3f}s wall time, {peak / 1024 / 1024:.2f} MB peak (traced)")
    return elapsed, peak


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        conn = get_connection(tmp_path / "db" / "bench.sqlite3")
        init_schema(conn)
        evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
        derived_store = DerivedStore(conn)
        project = evidence_store.create_project("Benchmark")
        derived_root = tmp_path / "derived"

        print(f"Benchmarking with {N_IMAGES} synthetic {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]} screenshots\n")

        evidence_list = _make_synthetic_evidence(evidence_store, project.id, tmp_path, N_IMAGES)

        def first_pass():
            for evidence in evidence_list:
                process_evidence(evidence, derived_root=derived_root, derived_store=derived_store)

        _measure("1. First-time processing (baseline)", first_pass)

        def reprocess_without_guard():
            # Simulates pre-Phase-11 behavior directly, bypassing the
            # idempotency check to show what unconditional reprocessing cost.
            for evidence in evidence_list:
                process_evidence(evidence, derived_root=derived_root, derived_store=derived_store, force=True)

        elapsed_without, _ = _measure("2. Reprocess WITHOUT idempotency guard (force=True)", reprocess_without_guard)

        def reprocess_with_guard():
            for evidence in evidence_list:
                process_evidence(evidence, derived_root=derived_root, derived_store=derived_store)  # force=False

        elapsed_with, _ = _measure("3. Reprocess WITH idempotency guard (Phase 11 default)", reprocess_with_guard)

        conn.close()

        print(f"\nSkipping already-processed evidence is {elapsed_without / max(elapsed_with, 1e-9):.1f}x faster "
              f"than unconditionally re-running OCR + preview generation.")


if __name__ == "__main__":
    main()
