"""
Doctor search tools: structured SQL search + semantic (FTS) search + re-ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.db.repository import Doctor, DoctorRepository, KeywordRepository
from src.logging_setup import setup_logging

logger = setup_logging("agent.tools")


@dataclass
class RankedDoctor:
    doctor: Doctor
    score: float
    reasons: list[str]

    def display(self) -> str:
        d = self.doctor
        slots = ", ".join(d.available_slots[:5]) if d.available_slots else "N/A"
        return (
            f"  Name:         {d.name}\n"
            f"  Expertise:    {d.expertise}\n"
            f"  Region:       {d.region or 'N/A'}\n"
            f"  Gender:       {d.gender or 'N/A'}\n"
            f"  Hospital:     {d.hospital_name or 'N/A'}\n"
            f"  Personality:  {d.personality or 'N/A'}\n"
            f"  Rating:       {d.rating:.1f}\n"
            f"  Available:    {slots}\n"
            f"  Match score:  {self.score:.2f} ({', '.join(self.reasons) or 'general match'})"
        )


class DoctorSearchTool:
    """Hybrid search: structured query and/or semantic/verbal query."""

    def __init__(self, doctors: DoctorRepository, keywords: KeywordRepository):
        self.doctors = doctors
        self.keywords = keywords

    def find_doctor(
        self,
        *,
        expertise: str | None = None,
        region: str | None = None,
        gender: str | None = None,
        personality: str | None = None,
        available_datetime: str | None = None,
        query: str | None = None,
        mode: str = "hybrid",
        limit: int = 10,
    ) -> list[RankedDoctor]:
        """
        FindDoctor(region, gender, expertise, ...)

        mode:
          - structured: SQL filters only
          - semantic: FTS / verbal query only
          - hybrid: both, merged and re-ranked
        """
        # Resolve expertise aliases
        if expertise:
            resolved = self.keywords.resolve(expertise)
            if resolved:
                expertise = resolved

        structured: list[Doctor] = []
        semantic: list[Doctor] = []

        if mode in ("structured", "hybrid"):
            structured = self.doctors.structured_search(
                expertise=expertise,
                region=region,
                gender=gender,
                personality=personality,
                limit=limit * 2,
            )
            # Progressive relaxation if too few results
            if len(structured) < 3 and expertise:
                broader = self.doctors.structured_search(
                    expertise=expertise,
                    region=region,
                    limit=limit * 2,
                )
                structured = _merge_doctors(structured, broader)
            if len(structured) < 3 and expertise:
                broader = self.doctors.structured_search(expertise=expertise, limit=limit * 2)
                structured = _merge_doctors(structured, broader)

        if mode in ("semantic", "hybrid"):
            verbal = query or " ".join(
                filter(None, [expertise, region, gender, personality, available_datetime])
            )
            if verbal:
                semantic = self.doctors.semantic_search(verbal, limit=limit * 2)

        if mode == "structured":
            candidates = structured
        elif mode == "semantic":
            candidates = semantic
        else:
            candidates = _merge_doctors(structured, semantic)

        ranked = self.rerank(
            candidates,
            expertise=expertise,
            region=region,
            gender=gender,
            personality=personality,
            available_datetime=available_datetime,
        )
        return ranked[:limit]

    def rerank(
        self,
        doctors: list[Doctor],
        *,
        expertise: str | None = None,
        region: str | None = None,
        gender: str | None = None,
        personality: str | None = None,
        available_datetime: str | None = None,
    ) -> list[RankedDoctor]:
        results: list[RankedDoctor] = []
        for d in doctors:
            score = 0.0
            reasons: list[str] = []

            if expertise and d.expertise:
                if expertise.lower() in d.expertise.lower() or d.expertise.lower() in expertise.lower():
                    score += 40
                    reasons.append("expertise")
                else:
                    score += 5

            if region and d.region:
                if region.lower() in d.region.lower() or d.region.lower() in region.lower():
                    score += 25
                    reasons.append("region")
                else:
                    score -= 5

            if gender and d.gender:
                if gender.lower() == d.gender.lower():
                    score += 15
                    reasons.append("gender")

            if personality and d.personality:
                if personality.lower() in d.personality.lower():
                    score += 10
                    reasons.append("personality")

            # Available time soft match
            if available_datetime and d.available_slots:
                if _slot_matches(available_datetime, d.available_slots):
                    score += 15
                    reasons.append("availability")
                else:
                    score -= 2

            # Prefer higher ratings
            score += min(float(d.rating or 0), 5.0) * 2
            score += min(float(d.score or 0), 10.0) * 0.5

            results.append(RankedDoctor(doctor=d, score=score, reasons=reasons))

        results.sort(key=lambda r: r.score, reverse=True)
        return results


def _merge_doctors(primary: list[Doctor], secondary: list[Doctor]) -> list[Doctor]:
    seen: set[int] = set()
    out: list[Doctor] = []
    for d in primary + secondary:
        if d.id is None:
            out.append(d)
            continue
        if d.id not in seen:
            seen.add(d.id)
            out.append(d)
    return out


def _slot_matches(want: str, slots: list[str]) -> bool:
    want_l = want.lower()
    for s in slots:
        s_l = s.lower()
        if want_l in s_l or s_l in want_l:
            return True
        # weekday name overlap
        weekdays = [
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        ]
        for w in weekdays:
            if w in want_l and w in s_l:
                return True
        # date yyyy-mm-dd
        m = re.search(r"\d{4}-\d{2}-\d{2}", want)
        if m and m.group(0) in s:
            return True
        try:
            # fuzzy: same date part
            for part in re.findall(r"\d{4}-\d{2}-\d{2}", s):
                if part in want:
                    return True
        except Exception:
            pass
    return False


def doctors_to_llm_context(ranked: list[RankedDoctor], top_n: int = 5) -> str:
    lines = []
    for i, r in enumerate(ranked[:top_n], 1):
        d = r.doctor
        lines.append(
            f"{i}. {d.name} | {d.expertise} | {d.gender} | {d.region} | "
            f"{d.hospital_name} | personality={d.personality} | rating={d.rating} | "
            f"slots={d.available_slots[:3]} | match={r.score:.1f}"
        )
    return "\n".join(lines) if lines else "(no doctors found)"
