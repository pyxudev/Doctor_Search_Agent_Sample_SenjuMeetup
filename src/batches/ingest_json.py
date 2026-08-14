"""
JSON data ingestion job.

Pipeline: JSON → Validation → Normalizer → DB → Set Index

Usage:
  python -m src.batches.ingest_json --source data/json/sample_doctors.json --init-db
  python -m src.batches.ingest_json --source data/json/incoming/doctors_update.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.config import get_settings
from src.db.connection import db_session, init_db, rebuild_fts
from src.db.repository import DoctorRepository, KeywordRepository
from src.logging_setup import setup_logging

logger = setup_logging("batches.ingest")


# --- Validation models ---


class HospitalIn(BaseModel):
    name: str
    region: str | None = None
    rating: float = 0.0
    address: str | None = None
    internal_number: str | None = None


class DoctorIn(BaseModel):
    name: str
    gender: str | None = None
    age: int | None = None
    expertise: str
    hospital: str | None = None
    hospital_name: str | None = None
    region: str | None = None
    language: str | None = None
    internal_number: str | None = None
    rating: float = 0.0
    score: float = 0.0
    personality: str | None = None
    available_slots: list[str] = Field(default_factory=list)
    available_datetime: list[str] | str | None = None
    register_date: str | None = None
    leave_date: str | None = None
    keywords: list[str] = Field(default_factory=list)
    aliases: list[Any] = Field(default_factory=list)
    hospital_address: str | None = None
    hospital_rating: float | None = None

    @field_validator("name", "expertise")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("must be non-empty")
        return str(v).strip()

    @field_validator("gender")
    @classmethod
    def normalize_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        g = v.strip().lower()
        mapping = {
            "m": "male",
            "f": "female",
            "male": "male",
            "female": "female",
            "other": "other",
            "unknown": "unknown",
        }
        return mapping.get(g, g)


class AliasIn(BaseModel):
    keyword: str
    alias: str


class IngestPayload(BaseModel):
    doctors: list[DoctorIn] = Field(default_factory=list)
    hospitals: list[HospitalIn] = Field(default_factory=list)
    aliases: list[AliasIn] = Field(default_factory=list)
    keywords: list[dict[str, Any]] = Field(default_factory=list)


# --- Normalizer ---


def normalize_doctor(doc: DoctorIn) -> dict[str, Any]:
    """Normalize validated doctor into DB-ready dict."""
    slots = list(doc.available_slots)
    if doc.available_datetime:
        if isinstance(doc.available_datetime, str):
            slots.extend(s.strip() for s in doc.available_datetime.split(",") if s.strip())
        else:
            slots.extend(doc.available_datetime)
    # dedupe preserve order
    seen: set[str] = set()
    uniq_slots: list[str] = []
    for s in slots:
        if s not in seen:
            seen.add(s)
            uniq_slots.append(s)

    hospital = (doc.hospital or doc.hospital_name or "").strip() or None
    expertise = doc.expertise.strip()
    # Title-case common expertise tokens lightly
    expertise_norm = expertise

    return {
        "name": doc.name.strip(),
        "gender": doc.gender,
        "age": doc.age,
        "expertise": expertise_norm,
        "hospital": hospital,
        "hospital_name": hospital,
        "region": doc.region.strip() if doc.region else None,
        "language": doc.language,
        "internal_number": doc.internal_number,
        "rating": float(doc.rating or 0),
        "score": float(doc.score or 0),
        "personality": doc.personality.strip().lower() if doc.personality else None,
        "available_slots": uniq_slots,
        "register_date": doc.register_date,
        "leave_date": doc.leave_date,
        "keywords": doc.keywords,
        "aliases": doc.aliases,
        "hospital_address": doc.hospital_address,
        "hospital_rating": doc.hospital_rating,
    }


DEFAULT_ALIASES = [
    ("Otolaryngology", "ENT"),
    ("Otolaryngology", "Ear Nose Throat"),
    ("Pediatrics", "Pedia"),
    ("Pediatrics", "Pediatric"),
    ("Pediatrics", "Pediatrician"),
    ("Pediatrics", "Paediatrician"),
    ("Pediatrics", "Child doctor"),
    ("Cardiology", "Heart"),
    ("Cardiology", "Cardio"),
    ("Cardiology", "Cardiologist"),
    ("Dermatology", "Skin"),
    ("Dermatology", "Derma"),
    ("Dermatology", "Dermatologist"),
    ("Dermatology", "Skin doctor"),
    ("Orthopedics", "Ortho"),
    ("Orthopedics", "Bone"),
    ("Orthopedics", "Orthopedist"),
    ("Neurology", "Neuro"),
    ("Neurology", "Brain"),
    ("Neurology", "Neurologist"),
    ("Psychiatry", "Mental health"),
    ("Psychiatry", "Psych"),
    ("Psychiatry", "Psychiatrist"),
    ("General Practice", "GP"),
    ("General Practice", "Family doctor"),
    ("General Practice", "Primary care"),
    ("Obstetrics and Gynecology", "OBGYN"),
    ("Obstetrics and Gynecology", "OB/GYN"),
    ("Obstetrics and Gynecology", "Gynecology"),
    ("Obstetrics and Gynecology", "Gynecologist"),
    ("Ophthalmology", "Eye"),
    ("Ophthalmology", "Eye doctor"),
    ("Ophthalmology", "Ophthalmologist"),
    ("Urology", "Urinary"),
    ("Urology", "Urologist"),
    ("Gastroenterology", "GI"),
    ("Gastroenterology", "Stomach"),
    ("Gastroenterology", "Gastroenterologist"),
]


def load_json_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    # Accept either full payload or a bare list of doctors
    if isinstance(data, list):
        return {"doctors": data}
    if not isinstance(data, dict):
        raise ValueError(f"Unsupported JSON root type: {type(data)}")
    return data


def validate_payload(data: dict[str, Any]) -> IngestPayload:
    return IngestPayload.model_validate(data)


def ingest_file(
    source: Path,
    *,
    init: bool = False,
    move_to_processed: bool = False,
    operator_name: str = "ingest_batch",
) -> dict[str, int]:
    settings = get_settings()
    if init:
        init_db()

    logger.info("Loading JSON from %s", source)
    raw = load_json_file(source)
    payload = validate_payload(raw)
    logger.info(
        "Validated: %d doctors, %d hospitals, %d aliases",
        len(payload.doctors),
        len(payload.hospitals),
        len(payload.aliases),
    )

    stats = {"doctors": 0, "hospitals": 0, "aliases": 0, "errors": 0}

    with db_session() as conn:
        docs = DoctorRepository(conn)
        kws = KeywordRepository(conn)

        # Seed default medical aliases once
        for keyword, alias in DEFAULT_ALIASES:
            kws.ensure_keyword(keyword, [alias])
            stats["aliases"] += 1

        for h in payload.hospitals:
            docs.hospitals.upsert(h.model_dump(), operator_name=operator_name)
            stats["hospitals"] += 1

        for a in payload.aliases:
            kws.ensure_keyword(a.keyword, [a.alias])
            stats["aliases"] += 1

        for item in payload.keywords:
            keyword = str(item.get("keyword", "")).strip()
            aliases = item.get("aliases") or item.get("alias") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            if keyword:
                kws.ensure_keyword(keyword, list(aliases))
                stats["aliases"] += 1

        for doc in payload.doctors:
            try:
                normalized = normalize_doctor(doc)
                docs.upsert(normalized, operator_name=operator_name)
                stats["doctors"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.exception("Failed to upsert doctor %s: %s", doc.name, exc)

        rebuild_fts(conn)

    logger.info("Ingest complete: %s", stats)

    if move_to_processed and source.exists():
        processed_dir = Path(settings.json_data_dir) / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = processed_dir / f"{source.stem}_{stamp}{source.suffix}"
        shutil.move(str(source), str(dest))
        logger.info("Moved %s → %s", source, dest)

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest doctor/hospital JSON into SQLite")
    parser.add_argument(
        "--source",
        "-s",
        required=True,
        help="Path to JSON file (doctors list or full payload)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database schema before ingest",
    )
    parser.add_argument(
        "--move-processed",
        action="store_true",
        help="Move source file to data/json/processed after success",
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        logger.error("Source file not found: %s", source)
        return 1

    try:
        stats = ingest_file(
            source,
            init=args.init_db,
            move_to_processed=args.move_processed,
        )
    except Exception as exc:
        logger.exception("Ingestion failed: %s", exc)
        return 1

    print(json.dumps({"status": "ok", "stats": stats}, indent=2))
    return 0 if stats.get("errors", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
