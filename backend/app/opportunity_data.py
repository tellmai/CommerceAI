"""Bulk fetch just the scoped records needed to rank candidate SKUs."""

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select, tuple_

from app.db import Entity
from app.selection import COHORT_FIELDS
from app.store import ReadSnapshot, query
from app.workflows import config_for


class Record(NamedTuple):
    id: str
    kind: str
    workspace: str
    version: int
    data: dict
    created_at: datetime
    updated_at: datetime


def read_records(db, statement):
    projected = statement.with_only_columns(*(getattr(Entity, key) for key in Record._fields))
    return [Record(*row) for row in db.execute(projected)]


def by_ids(db, workspace, ids):
    return (
        read_records(db, select(Entity).where(Entity.workspace == workspace, Entity.id.in_(ids)))
        if ids
        else []
    )


def related(db, workspace, kind, field, ids, *latest_by):
    if not ids:
        return []
    column = Entity.data[field].as_string()
    stmt = query(workspace, kind).where(column.in_(ids))
    if latest_by:
        keys = (column, *(Entity.data[key].as_string() for key in latest_by if key != field))
        ranked = stmt.with_only_columns(
            Entity.id,
            func.row_number()
            .over(partition_by=keys, order_by=(Entity.updated_at.desc(), Entity.id))
            .label("rank"),
        ).subquery()
        stmt = select(Entity).join(ranked, Entity.id == ranked.c.id).where(ranked.c.rank == 1)
    return read_records(db, stmt)


def opportunity_snapshot(db, workspace, search):
    candidates = [
        row
        for row in read_records(db, query(workspace, "candidates"))
        if row.data["state"] not in {"ARCHIVED", "REJECTED"}
    ]
    skus = by_ids(db, workspace, {row.data["sku_id"] for row in candidates})
    products = by_ids(db, workspace, {row.data["product_id"] for row in skus})
    keywords = by_ids(
        db, workspace, {row.data["keyword_id"] for row in candidates if row.data.get("keyword_id")}
    )
    names = {row.id: row.data for row in [*skus, *products, *keywords]}
    if search:
        candidates = [
            row
            for row in candidates
            if any(
                search in term.casefold()
                for term in (
                    names[row.data["sku_id"]]["name"],
                    names[names[row.data["sku_id"]]["product_id"]]["category"],
                    names.get(row.data.get("keyword_id"), {}).get("keyword", ""),
                )
            )
        ]
    if not candidates:
        return [], ReadSnapshot(workspace, []), {}
    ids = {row.id for row in candidates}
    offers = by_ids(
        db,
        workspace,
        {
            row.data[field]
            for row in candidates
            for field in ("primary_offer_id", "backup_offer_id")
            if row.data.get(field)
        },
    )
    scores = related(db, workspace, "scores", "candidate_id", ids, "candidate_id")
    matches = related(db, workspace, "matches", "sku_id", {row.data["sku_id"] for row in candidates})
    listing_ids = {row.data["listing_id"] for row in matches}
    records = [
        *candidates,
        *skus,
        *products,
        *keywords,
        *offers,
        *scores,
        *matches,
        *by_ids(db, workspace, listing_ids),
        *related(db, workspace, "prices", "listing_id", listing_ids),
        *related(db, workspace, "risks", "candidate_id", ids, "risk_code"),
        *related(db, workspace, "profits", "candidate_id", ids, "label"),
        *related(db, workspace, "samples", "offer_id", {row.id for row in offers}, "offer_id"),
        config_for(db, workspace),
    ]
    seen = {row.id for row in records}
    referenced = {item["id"] for score in scores for item in score.data.get("records", [])}
    records.extend(by_ids(db, workspace, referenced - seen))
    evidence = by_ids(
        db, workspace, {row.data["evidence_id"] for row in records if row.data.get("evidence_id")}
    )
    sources = by_ids(
        db, workspace, {row.data["source_id"] for row in [*records, *evidence] if row.data.get("source_id")}
    )
    reports = by_ids(
        db, workspace, {row.data["market_report_id"] for row in evidence if row.data.get("market_report_id")}
    )
    runs = by_ids(db, workspace, {row.data["market_run_id"] for row in reports})
    cohorts = {
        tuple(names[row.data["keyword_id"]][field] for field in COHORT_FIELDS)
        for row in candidates
        if row.data.get("keyword_id")
    }
    latest_dates = {}
    if cohorts:
        columns = [Entity.data[field].as_string() for field in COHORT_FIELDS]
        statement = (
            query(workspace, "keywords")
            .with_only_columns(*columns, func.max(Entity.created_at))
            .where(tuple_(*columns).in_(cohorts))
            .group_by(*columns)
        )
        latest_dates = {tuple(record[:-1]): record[-1] for record in db.execute(statement)}
    return candidates, ReadSnapshot(workspace, [*records, *evidence, *sources, *reports, *runs]), latest_dates
