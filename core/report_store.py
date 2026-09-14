"""Report plan persistence.

One active plan per project (enforced by a UNIQUE constraint on
report_plans.project_id) — creating a second plan raises rather than
silently overwriting; delete_plan() is the explicit, deliberate reset path.

Evidence and finding placement is validated against the actual project's
evidence/findings stores when provided, so a plan can't reference an item
that doesn't exist or belongs to a different project.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.report_templates import TEMPLATES

VALID_ITEM_TYPES = {"evidence", "finding", "text"}


@dataclass
class ReportItem:
    id: int
    report_section_id: int
    item_type: str
    order_index: int
    caption: str = ""
    included: bool = True
    evidence_id: str | None = None
    finding_id: str | None = None
    text_content: str | None = None


@dataclass
class ReportSection:
    id: int
    report_plan_id: int
    title: str
    order_index: int
    included: bool = True
    items: list[ReportItem] = field(default_factory=list)


@dataclass
class ReportPlan:
    id: int
    project_id: str
    template_type: str
    created_at: str
    updated_at: str
    sections: list[ReportSection] = field(default_factory=list)


class ReportStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------- Plan lifecycle ----------

    def create_plan(self, project_id: str, template_type: str) -> ReportPlan:
        if template_type not in TEMPLATES:
            raise ValueError(f"Unknown template_type: {template_type}. Must be one of {list(TEMPLATES)}")

        existing = self.conn.execute(
            "SELECT id FROM report_plans WHERE project_id = ?", (project_id,)
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"A report plan already exists for project {project_id}. "
                "Use get_plan() to read it or delete_plan() to reset it deliberately."
            )

        now = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            "INSERT INTO report_plans (project_id, template_type, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, template_type, now, now),
        )
        plan_id = cur.lastrowid

        for index, title in enumerate(TEMPLATES[template_type]):
            self.conn.execute(
                "INSERT INTO report_sections (report_plan_id, title, order_index, included, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (plan_id, title, index, now),
            )

        return self.get_plan(project_id)

    def delete_plan(self, project_id: str) -> None:
        plan = self.conn.execute(
            "SELECT id FROM report_plans WHERE project_id = ?", (project_id,)
        ).fetchone()
        if plan is None:
            return
        plan_id = plan["id"]
        section_ids = [
            r["id"] for r in self.conn.execute(
                "SELECT id FROM report_sections WHERE report_plan_id = ?", (plan_id,)
            ).fetchall()
        ]
        for section_id in section_ids:
            self.conn.execute("DELETE FROM report_items WHERE report_section_id = ?", (section_id,))
        self.conn.execute("DELETE FROM report_sections WHERE report_plan_id = ?", (plan_id,))
        self.conn.execute("DELETE FROM report_plans WHERE id = ?", (plan_id,))

    def get_plan(self, project_id: str) -> ReportPlan | None:
        row = self.conn.execute(
            "SELECT * FROM report_plans WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return None

        plan = ReportPlan(
            id=row["id"], project_id=row["project_id"], template_type=row["template_type"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
        section_rows = self.conn.execute(
            "SELECT * FROM report_sections WHERE report_plan_id = ? ORDER BY order_index",
            (plan.id,),
        ).fetchall()
        for srow in section_rows:
            section = ReportSection(
                id=srow["id"], report_plan_id=srow["report_plan_id"], title=srow["title"],
                order_index=srow["order_index"], included=bool(srow["included"]),
            )
            item_rows = self.conn.execute(
                "SELECT * FROM report_items WHERE report_section_id = ? ORDER BY order_index",
                (section.id,),
            ).fetchall()
            section.items = [
                ReportItem(
                    id=irow["id"], report_section_id=irow["report_section_id"],
                    item_type=irow["item_type"], order_index=irow["order_index"],
                    caption=irow["caption"], included=bool(irow["included"]),
                    evidence_id=irow["evidence_id"], finding_id=irow["finding_id"],
                    text_content=irow["text_content"],
                )
                for irow in item_rows
            ]
            plan.sections.append(section)
        return plan

    # ---------- Sections ----------

    def add_section(self, project_id: str, title: str) -> ReportSection:
        plan = self._require_plan(project_id)
        now = datetime.now(UTC).isoformat()
        next_index = len(plan.sections)
        cur = self.conn.execute(
            "INSERT INTO report_sections (report_plan_id, title, order_index, included, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (plan.id, title, next_index, now),
        )
        return next(s for s in self.get_plan(project_id).sections if s.id == cur.lastrowid)

    def reorder_sections(self, project_id: str, ordered_section_ids: list[int]) -> None:
        plan = self._require_plan(project_id)
        current_ids = {s.id for s in plan.sections}
        if set(ordered_section_ids) != current_ids:
            raise ValueError(
                "reorder_sections requires exactly the plan's current section IDs, "
                "in the desired order — use add_section/delete to change membership."
            )
        for index, section_id in enumerate(ordered_section_ids):
            self.conn.execute(
                "UPDATE report_sections SET order_index = ? WHERE id = ?", (index, section_id)
            )

    def set_section_included(self, section_id: int, included: bool) -> None:
        self.conn.execute(
            "UPDATE report_sections SET included = ? WHERE id = ?", (1 if included else 0, section_id)
        )

    # ---------- Items ----------

    def add_evidence_item(
        self, project_id: str, section_id: int, evidence_id: str,
        caption: str = "", evidence_store: EvidenceStore | None = None,
    ) -> ReportItem:
        if evidence_store is not None and evidence_store.get_evidence(project_id, evidence_id) is None:
            raise ValueError(f"Unknown evidence_id for this project: {evidence_id}")
        return self._add_item(section_id, "evidence", caption, evidence_id=evidence_id)

    def add_finding_item(
        self, project_id: str, section_id: int, finding_id: str,
        caption: str = "", findings_store: FindingsStore | None = None,
    ) -> ReportItem:
        if findings_store is not None and findings_store.get_finding(project_id, finding_id) is None:
            raise ValueError(f"Unknown finding_id for this project: {finding_id}")
        return self._add_item(section_id, "finding", caption, finding_id=finding_id)

    def add_text_item(self, section_id: int, text_content: str, caption: str = "") -> ReportItem:
        return self._add_item(section_id, "text", caption, text_content=text_content)

    def _add_item(
        self, section_id: int, item_type: str, caption: str,
        evidence_id: str | None = None, finding_id: str | None = None, text_content: str | None = None,
    ) -> ReportItem:
        now = datetime.now(UTC).isoformat()
        next_index = self.conn.execute(
            "SELECT COUNT(*) AS c FROM report_items WHERE report_section_id = ?", (section_id,)
        ).fetchone()["c"]
        cur = self.conn.execute(
            """
            INSERT INTO report_items
                (report_section_id, item_type, evidence_id, finding_id, text_content, caption, order_index, included, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (section_id, item_type, evidence_id, finding_id, text_content, caption, next_index, now),
        )
        return self.get_item(cur.lastrowid)

    def get_item(self, item_id: int) -> ReportItem | None:
        row = self.conn.execute("SELECT * FROM report_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        return ReportItem(
            id=row["id"], report_section_id=row["report_section_id"], item_type=row["item_type"],
            order_index=row["order_index"], caption=row["caption"], included=bool(row["included"]),
            evidence_id=row["evidence_id"], finding_id=row["finding_id"], text_content=row["text_content"],
        )

    def reorder_items(self, section_id: int, ordered_item_ids: list[int]) -> None:
        current_ids = {
            r["id"] for r in self.conn.execute(
                "SELECT id FROM report_items WHERE report_section_id = ?", (section_id,)
            ).fetchall()
        }
        if set(ordered_item_ids) != current_ids:
            raise ValueError(
                "reorder_items requires exactly this section's current item IDs, in the desired order."
            )
        for index, item_id in enumerate(ordered_item_ids):
            self.conn.execute("UPDATE report_items SET order_index = ? WHERE id = ?", (index, item_id))

    def set_item_included(self, item_id: int, included: bool) -> None:
        self.conn.execute(
            "UPDATE report_items SET included = ? WHERE id = ?", (1 if included else 0, item_id)
        )

    def set_item_caption(self, item_id: int, caption: str) -> None:
        self.conn.execute("UPDATE report_items SET caption = ? WHERE id = ?", (caption, item_id))

    def _require_plan(self, project_id: str) -> ReportPlan:
        plan = self.get_plan(project_id)
        if plan is None:
            raise ValueError(f"No report plan exists for project {project_id}. Call create_plan() first.")
        return plan
