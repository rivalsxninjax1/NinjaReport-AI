"""Default section lists for the two report templates.

These are just starting points — the report builder lets sections be
added, removed (excluded), and reordered after bootstrapping.
"""
from __future__ import annotations

CTF_TEMPLATE_SECTIONS = [
    "Cover",
    "Introduction & Objective",
    "Methodology",
    "Attack Path / Walkthrough",
    "Findings Summary",
    "Flags Captured",
    "Evidence Appendix",
    "Conclusion",
]

VAPT_TEMPLATE_SECTIONS = [
    "Cover",
    "Document Control",
    "Executive Summary",
    "Scope",
    "Methodology",
    "Tools",
    "Attack Narrative",
    "Findings Summary",
    "Detailed Findings",
    "Conclusion",
    "Evidence Appendix",
]

TEMPLATES = {
    "ctf": CTF_TEMPLATE_SECTIONS,
    "vapt": VAPT_TEMPLATE_SECTIONS,
}
