"""
Input validation for patient messages.

- Confidence score
- Medical term resolution via keywords/aliases
- Fuzzy matching (Levenshtein via rapidfuzz)
- Confirmation flow helpers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process

from src.config import get_settings
from src.db.repository import KeywordRepository
from src.logging_setup import setup_logging

logger = setup_logging("agent.validation")


@dataclass
class ExtractedNeeds:
    gender: str | None = None
    expertise: str | None = None
    region: str | None = None
    personality: str | None = None
    available_datetime: str | None = None
    raw_terms: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_confirmation: bool = True
    notes: list[str] = field(default_factory=list)

    def missing_required(self) -> list[str]:
        missing = []
        if not self.expertise:
            missing.append("expertise / illness specialty")
        if not self.region:
            missing.append("region")
        return missing

    def to_keywords_str(self) -> str:
        parts = [
            p
            for p in [
                self.expertise,
                self.region,
                self.gender,
                self.personality,
                self.available_datetime,
            ]
            if p
        ]
        return ", ".join(parts)

    def as_search_params(self) -> dict[str, Any]:
        return {
            "expertise": self.expertise,
            "region": self.region,
            "gender": self.gender,
            "personality": self.personality,
            "available_datetime": self.available_datetime,
        }


GENDER_WORDS = {
    "male": "male",
    "man": "male",
    "men": "male",
    "gentleman": "male",
    "female": "female",
    "woman": "female",
    "women": "female",
    "lady": "female",
}

PERSONALITY_WORDS = [
    "kind",
    "humor",
    "humorous",
    "funny",
    "professional",
    "gentle",
    "patient",
    "friendly",
    "empathetic",
    "warm",
    "strict",
    "calm",
]


class InputValidator:
    def __init__(self, keywords: KeywordRepository):
        self.keywords = keywords
        self.settings = get_settings()
        self._alias_map: dict[str, str] = {}
        self._refresh_aliases()

    def _refresh_aliases(self) -> None:
        self._alias_map = {}
        for keyword, alias in self.keywords.all_pairs():
            self._alias_map[alias.lower()] = keyword
            self._alias_map[keyword.lower()] = keyword

    def resolve_medical_term(self, term: str) -> tuple[str | None, float]:
        """Return (canonical_keyword, confidence 0-1)."""
        term = term.strip()
        if not term:
            return None, 0.0

        exact = self.keywords.resolve(term)
        if exact:
            return exact, 1.0

        if not self._alias_map:
            return None, 0.0

        choices = list(self._alias_map.keys())
        match = process.extractOne(
            term.lower(),
            choices,
            scorer=fuzz.WRatio,
            score_cutoff=70,
        )
        if not match:
            return None, 0.0
        matched_alias, score, _ = match
        canonical = self._alias_map[matched_alias]
        confidence = float(score) / 100.0
        return canonical, confidence

    def extract_rule_based(self, text: str) -> ExtractedNeeds:
        """Lightweight extraction without LLM (always available)."""
        import re

        self._refresh_aliases()
        lower = text.lower()
        needs = ExtractedNeeds()
        confidences: list[float] = []

        # Gender
        for word, gender in GENDER_WORDS.items():
            if re_word_boundary(word, lower):
                needs.gender = gender
                confidences.append(0.95)
                break

        # Personality
        for p in PERSONALITY_WORDS:
            if re_word_boundary(p, lower):
                needs.personality = "humor" if p in ("humor", "humorous", "funny") else p
                confidences.append(0.9)
                break

        # Region: "in/near/around/region/area/from <Place>"
        # Capture one or two place tokens, never time words
        time_tokens = {
            "tomorrow", "today", "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday", "next", "this", "morning",
            "afternoon", "evening", "available", "who", "with", "that", "for",
            "at", "a", "an", "the", "my",
        }
        region_match = re.search(
            r"\b(?:in|near|around|region|area|from)\s+([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+)?)",
            text,
            re.I,
        )
        if region_match:
            parts = [
                p for p in region_match.group(1).split()
                if p.lower() not in time_tokens and p.lower() not in GENDER_WORDS
                and p.lower() not in PERSONALITY_WORDS
            ]
            if parts:
                region = " ".join(parts)
                needs.region = region.title() if region.islower() else region
                confidences.append(0.9)

        # Available datetime hints
        dt_match = re.search(
            r"\b((?:tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
            r"next week|this week|morning|afternoon|evening)"
            r"(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)",
            text,
            re.I,
        )
        if dt_match:
            needs.available_datetime = dt_match.group(1).strip()
            confidences.append(0.85)

        # Normalize common patient phrasings before alias match
        expanded = _expand_colloquial(text)

        # Expertise via alias fuzzy match on tokens and n-grams
        tokens = re.findall(r"[A-Za-z]+", expanded)
        skip = {
            "in", "near", "around", "region", "area", "from", "need", "want",
            "doctor", "please", "looking", "for", "a", "the", "with", "who",
            "is", "and", "or", "my", "i", "me", "have", "has", "available",
            "tomorrow", "today", "kind", "female", "male", "woman", "man",
        }
        candidates: list[tuple[str, float]] = []
        for n in (3, 2, 1):
            for i in range(len(tokens) - n + 1):
                phrase = " ".join(tokens[i : i + n])
                if phrase.lower() in GENDER_WORDS or phrase.lower() in PERSONALITY_WORDS:
                    continue
                if phrase.lower() in skip:
                    continue
                if needs.region and phrase.lower() == needs.region.lower():
                    continue
                canon, conf = self.resolve_medical_term(phrase)
                if canon and conf >= 0.7:
                    candidates.append((canon, conf))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            needs.expertise = candidates[0][0]
            confidences.append(candidates[0][1])
            needs.raw_terms = [c[0] for c in candidates[:5]]

        if confidences:
            needs.confidence = sum(confidences) / len(confidences)
        else:
            needs.confidence = 0.3
            needs.notes.append("Could not extract clear medical terms")

        threshold = self.settings.confidence_threshold
        needs.needs_confirmation = (
            needs.confidence < threshold or bool(needs.missing_required())
        )
        return needs

    def merge_llm_extraction(
        self, base: ExtractedNeeds, llm_data: dict[str, Any]
    ) -> ExtractedNeeds:
        """Merge LLM structured extraction into rule-based needs."""
        for key in ("gender", "expertise", "region", "personality", "available_datetime"):
            val = llm_data.get(key)
            if val and not getattr(base, key):
                if key == "expertise":
                    canon, conf = self.resolve_medical_term(str(val))
                    setattr(base, key, canon or str(val))
                    if conf:
                        base.confidence = max(base.confidence, conf)
                elif key == "gender":
                    g = str(val).lower()
                    setattr(base, key, GENDER_WORDS.get(g, g))
                else:
                    setattr(base, key, str(val).strip())
            elif val and key == "expertise":
                # Prefer resolved form even if base has something
                canon, conf = self.resolve_medical_term(str(val))
                if canon and conf >= 0.7:
                    base.expertise = canon
                    base.confidence = max(base.confidence, conf)

        if "confidence" in llm_data:
            try:
                base.confidence = max(base.confidence, float(llm_data["confidence"]))
            except (TypeError, ValueError):
                pass

        threshold = self.settings.confidence_threshold
        base.needs_confirmation = (
            base.confidence < threshold or bool(base.missing_required())
        )
        return base


def re_word_boundary(word: str, text_lower: str) -> bool:
    import re

    return bool(re.search(rf"\b{re.escape(word)}\b", text_lower))


# Map everyday / role nouns to specialty-ish search phrases
_COLLOQUIAL = {
    "pediatrician": "Pediatrics",
    "paediatrician": "Pediatrics",
    "pedia": "Pediatrics",
    "cardiologist": "Cardiology",
    "dermatologist": "Dermatology",
    "ent": "Otolaryngology",
    "orthopedist": "Orthopedics",
    "orthopaedist": "Orthopedics",
    "neurologist": "Neurology",
    "psychiatrist": "Psychiatry",
    "gp": "General Practice",
    "family doctor": "General Practice",
    "obgyn": "Obstetrics and Gynecology",
    "gynecologist": "Obstetrics and Gynecology",
    "ophthalmologist": "Ophthalmology",
    "eye doctor": "Ophthalmology",
    "urologist": "Urology",
    "gastroenterologist": "Gastroenterology",
    "skin doctor": "Dermatology",
    "heart doctor": "Cardiology",
    "child doctor": "Pediatrics",
}


def _expand_colloquial(text: str) -> str:
    """Replace colloquial role words with specialty names for better matching."""
    import re

    out = text
    # Longer phrases first
    for phrase, specialty in sorted(_COLLOQUIAL.items(), key=lambda x: -len(x[0])):
        out = re.sub(rf"\b{re.escape(phrase)}\b", specialty, out, flags=re.I)
    return out
