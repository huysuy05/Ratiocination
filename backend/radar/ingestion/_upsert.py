"""Batched, idempotent upserts of connector records (SPEC §2.1 database rules). Owner: ingest.

Network work happens before this is called; each batch is one short write transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from radar.store.papers import PaperRecord, upsert_paper

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

BATCH_SIZE = 200


@dataclass
class UpsertCounts:
    records: int = 0
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    new_versions: int = 0
    duplicates: int = 0
    paper_ids: list[int] = field(default_factory=list)

    def add(self, other: UpsertCounts) -> None:
        self.records += other.records
        self.created += other.created
        self.changed += other.changed
        self.unchanged += other.unchanged
        self.new_versions += other.new_versions
        self.duplicates += other.duplicates
        self.paper_ids.extend(other.paper_ids)

    def as_stats(self, prefix: str = "") -> dict[str, int]:
        return {
            f"{prefix}records": self.records,
            f"{prefix}created": self.created,
            f"{prefix}changed": self.changed,
            f"{prefix}unchanged": self.unchanged,
            f"{prefix}new_versions": self.new_versions,
            f"{prefix}duplicates": self.duplicates,
        }


def upsert_records(ctx: JobContext, records: Sequence[PaperRecord]) -> UpsertCounts:
    """Upsert ``records`` in write batches of :data:`BATCH_SIZE`; returns counts and paper ids."""
    counts = UpsertCounts()
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start : start + BATCH_SIZE]
        with ctx.session(write=True) as session:
            for record in batch:
                result = upsert_paper(session, record)
                counts.records += 1
                counts.paper_ids.append(result.paper_id)
                if result.created:
                    counts.created += 1
                elif result.changed_fields:
                    counts.changed += 1
                else:
                    counts.unchanged += 1
                if result.new_version:
                    counts.new_versions += 1
                if result.duplicate_candidate_of is not None:
                    counts.duplicates += 1
    return counts
