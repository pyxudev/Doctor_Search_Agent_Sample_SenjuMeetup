"""Data access layer for doctors, hospitals, keywords, audit, and contact logs."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Doctor:
    id: int | None = None
    name: str = ""
    gender: str | None = None
    age: int | None = None
    expertise: str = ""
    hospital_id: int | None = None
    hospital_name: str | None = None
    region: str | None = None
    language: str | None = None
    internal_number: str | None = None
    rating: float = 0.0
    score: float = 0.0
    personality: str | None = None
    available_slots: list[str] = field(default_factory=list)
    register_date: str | None = None
    leave_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(
        self,
        process_name: str,
        table_name: str,
        value_before: Any = None,
        value_after: Any = None,
        operator_name: str = "system",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO audit_log (audit_datetime, process_name, table_name, value_before, value_after, operator_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                process_name,
                table_name,
                json.dumps(value_before, ensure_ascii=False) if value_before is not None else None,
                json.dumps(value_after, ensure_ascii=False) if value_after is not None else None,
                operator_name,
            ),
        )
        return int(cur.lastrowid)


class KeywordRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, keyword: str, alias: str) -> None:
        self.conn.execute(
            """
            INSERT INTO keywords_alias (keyword, alias)
            VALUES (?, ?)
            ON CONFLICT(keyword, alias) DO NOTHING
            """,
            (keyword.strip(), alias.strip()),
        )

    def ensure_keyword(self, keyword: str, aliases: list[str] | None = None) -> None:
        keyword = keyword.strip()
        if not keyword:
            return
        self.upsert(keyword, keyword)
        for a in aliases or []:
            if a and a.strip():
                self.upsert(keyword, a.strip())

    def resolve(self, term: str) -> str | None:
        """Map alias/term to canonical keyword, or None if unknown."""
        term = term.strip()
        if not term:
            return None
        row = self.conn.execute(
            """
            SELECT keyword FROM keywords_alias
            WHERE lower(alias) = lower(?) OR lower(keyword) = lower(?)
            LIMIT 1
            """,
            (term, term),
        ).fetchone()
        return row["keyword"] if row else None

    def all_pairs(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT keyword, alias FROM keywords_alias ORDER BY keyword, alias"
        ).fetchall()
        return [(r["keyword"], r["alias"]) for r in rows]

    def list_keywords(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT keyword FROM keywords_alias ORDER BY keyword"
        ).fetchall()
        return [r["keyword"] for r in rows]


class HospitalRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.audit = AuditRepository(conn)

    def upsert(self, data: dict[str, Any], operator_name: str = "system") -> int:
        name = data["name"].strip()
        existing = self.conn.execute(
            "SELECT * FROM hospital WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            before = dict(existing)
            self.conn.execute(
                """
                UPDATE hospital
                SET region = ?, rating = ?, address = ?, internal_number = ?
                WHERE id = ?
                """,
                (
                    data.get("region"),
                    float(data.get("rating") or 0),
                    data.get("address"),
                    data.get("internal_number"),
                    existing["id"],
                ),
            )
            after = dict(self.conn.execute("SELECT * FROM hospital WHERE id = ?", (existing["id"],)).fetchone())
            self.audit.log("upsert_hospital", "hospital", before, after, operator_name)
            return int(existing["id"])

        cur = self.conn.execute(
            """
            INSERT INTO hospital (name, region, rating, address, internal_number)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                data.get("region"),
                float(data.get("rating") or 0),
                data.get("address"),
                data.get("internal_number"),
            ),
        )
        hid = int(cur.lastrowid)
        self.audit.log("create_hospital", "hospital", None, data, operator_name)
        return hid

    def get_by_name(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM hospital WHERE name = ?", (name,)
        ).fetchone()


class DoctorRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.audit = AuditRepository(conn)
        self.keywords = KeywordRepository(conn)
        self.hospitals = HospitalRepository(conn)

    def _row_to_doctor(self, row: sqlite3.Row) -> Doctor:
        slots_raw = row["available_slots"]
        try:
            slots = json.loads(slots_raw) if slots_raw else []
        except json.JSONDecodeError:
            slots = []
        return Doctor(
            id=row["id"],
            name=row["name"],
            gender=row["gender"],
            age=row["age"],
            expertise=row["expertise"],
            hospital_id=row["hospital_id"],
            hospital_name=row["hospital_name"],
            region=row["region"],
            language=row["language"],
            internal_number=row["internal_number"],
            rating=row["rating"] or 0.0,
            score=row["score"] or 0.0,
            personality=row["personality"],
            available_slots=slots,
            register_date=row["register_date"],
            leave_date=row["leave_date"],
        )

    def upsert(self, data: dict[str, Any], operator_name: str = "system") -> int:
        hospital_name = (data.get("hospital") or data.get("hospital_name") or "").strip()
        hospital_id = None
        if hospital_name:
            hospital_id = self.hospitals.upsert(
                {
                    "name": hospital_name,
                    "region": data.get("region"),
                    "rating": data.get("hospital_rating", data.get("rating", 0)),
                    "address": data.get("hospital_address") or data.get("address"),
                    "internal_number": data.get("hospital_internal_number"),
                },
                operator_name=operator_name,
            )

        name = data["name"].strip()
        expertise = (data.get("expertise") or "").strip()
        existing = self.conn.execute(
            """
            SELECT * FROM doctor
            WHERE lower(name) = lower(?) AND lower(expertise) = lower(?)
              AND coalesce(lower(hospital_name), '') = lower(?)
            """,
            (name, expertise, hospital_name),
        ).fetchone()

        slots = data.get("available_slots") or data.get("available_datetime") or []
        if isinstance(slots, str):
            slots = [s.strip() for s in slots.split(",") if s.strip()]
        slots_json = json.dumps(slots, ensure_ascii=False)

        values = (
            name,
            data.get("gender"),
            data.get("age"),
            expertise,
            hospital_id,
            hospital_name or None,
            data.get("region"),
            data.get("language"),
            data.get("internal_number"),
            float(data.get("rating") or 0),
            float(data.get("score") or 0),
            data.get("personality"),
            slots_json,
            data.get("register_date") or _now()[:10],
            data.get("leave_date"),
        )

        if existing:
            before = dict(existing)
            self.conn.execute(
                """
                UPDATE doctor SET
                    name=?, gender=?, age=?, expertise=?, hospital_id=?, hospital_name=?,
                    region=?, language=?, internal_number=?, rating=?, score=?,
                    personality=?, available_slots=?, register_date=?, leave_date=?
                WHERE id=?
                """,
                values + (existing["id"],),
            )
            after = dict(self.conn.execute("SELECT * FROM doctor WHERE id = ?", (existing["id"],)).fetchone())
            self.audit.log("update_doctor", "doctor", before, after, operator_name)
            doc_id = int(existing["id"])
        else:
            cur = self.conn.execute(
                """
                INSERT INTO doctor (
                    name, gender, age, expertise, hospital_id, hospital_name,
                    region, language, internal_number, rating, score,
                    personality, available_slots, register_date, leave_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            doc_id = int(cur.lastrowid)
            self.audit.log("create_doctor", "doctor", None, data, operator_name)

        # Auto-register expertise (and simple aliases) as keywords
        if expertise:
            self.keywords.ensure_keyword(expertise)
        for extra_kw in data.get("keywords") or []:
            self.keywords.ensure_keyword(str(extra_kw))
        for alias_pair in data.get("aliases") or []:
            if isinstance(alias_pair, dict):
                self.keywords.ensure_keyword(
                    alias_pair.get("keyword", expertise),
                    [alias_pair.get("alias", "")],
                )
            elif isinstance(alias_pair, (list, tuple)) and len(alias_pair) == 2:
                self.keywords.ensure_keyword(alias_pair[0], [alias_pair[1]])

        return doc_id

    def structured_search(
        self,
        expertise: str | None = None,
        region: str | None = None,
        gender: str | None = None,
        personality: str | None = None,
        hospital: str | None = None,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[Doctor]:
        clauses = ["1=1"]
        params: list[Any] = []

        if active_only:
            clauses.append("(leave_date IS NULL OR leave_date = '' OR leave_date > date('now'))")

        if expertise:
            clauses.append("lower(expertise) LIKE lower(?)")
            params.append(f"%{expertise}%")
        if region:
            clauses.append("lower(region) LIKE lower(?)")
            params.append(f"%{region}%")
        if gender:
            clauses.append("lower(gender) = lower(?)")
            params.append(gender)
        if personality:
            clauses.append("lower(personality) LIKE lower(?)")
            params.append(f"%{personality}%")
        if hospital:
            clauses.append("lower(hospital_name) LIKE lower(?)")
            params.append(f"%{hospital}%")

        sql = f"""
            SELECT * FROM doctor
            WHERE {' AND '.join(clauses)}
            ORDER BY rating DESC, score DESC
            LIMIT ?
        """
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_doctor(r) for r in rows]

    def semantic_search(self, query: str, limit: int = 20) -> list[Doctor]:
        """FTS5 search; falls back to LIKE if FTS fails."""
        if not query.strip():
            return []
        # Escape FTS special chars lightly
        safe = " ".join(query.replace('"', " ").split())
        try:
            rows = self.conn.execute(
                """
                SELECT d.* FROM doctor d
                JOIN doctor_fts f ON d.id = f.rowid
                WHERE doctor_fts MATCH ?
                  AND (d.leave_date IS NULL OR d.leave_date = '' OR d.leave_date > date('now'))
                ORDER BY rank
                LIMIT ?
                """,
                (safe, limit),
            ).fetchall()
            if rows:
                return [self._row_to_doctor(r) for r in rows]
        except sqlite3.OperationalError:
            pass

        # Fallback: multi-token LIKE
        tokens = [t for t in safe.split() if len(t) > 1]
        if not tokens:
            return []
        clauses = []
        params: list[Any] = []
        for t in tokens:
            clauses.append(
                "(lower(name) LIKE lower(?) OR lower(expertise) LIKE lower(?) "
                "OR lower(region) LIKE lower(?) OR lower(personality) LIKE lower(?) "
                "OR lower(hospital_name) LIKE lower(?))"
            )
            like = f"%{t}%"
            params.extend([like, like, like, like, like])
        sql = f"""
            SELECT * FROM doctor
            WHERE ({' OR '.join(clauses)})
              AND (leave_date IS NULL OR leave_date = '' OR leave_date > date('now'))
            ORDER BY rating DESC
            LIMIT ?
        """
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_doctor(r) for r in rows]

    def get_by_id(self, doctor_id: int) -> Doctor | None:
        row = self.conn.execute("SELECT * FROM doctor WHERE id = ?", (doctor_id,)).fetchone()
        return self._row_to_doctor(row) if row else None

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM doctor").fetchone()
        return int(row["c"])


class ContactLogRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def start(self, phone_number: str | None = None, keywords: str | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO contact_log (start_time, phone_number, keywords)
            VALUES (?, ?, ?)
            """,
            (_now(), phone_number, keywords),
        )
        return int(cur.lastrowid)

    def finish(
        self,
        contact_id: int,
        doctor_name: str | None = None,
        is_accurate: int | None = None,
        csat: float | None = None,
        token_cost: int = 0,
        keywords: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE contact_log
            SET end_time = ?, doctor_name = COALESCE(?, doctor_name),
                is_accurate = COALESCE(?, is_accurate),
                csat = COALESCE(?, csat),
                token_cost = token_cost + ?,
                keywords = COALESCE(?, keywords)
            WHERE id = ?
            """,
            (_now(), doctor_name, is_accurate, csat, token_cost, keywords, contact_id),
        )
