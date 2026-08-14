"""
Intent detection, planner, and multi-turn agent loop.

Flow: Intent → Planner → Tool Calling → FindDoctor → Re-ranking → Response
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.agent.llm import get_llm
from src.agent.tools import DoctorSearchTool, RankedDoctor, doctors_to_llm_context
from src.agent.validation import ExtractedNeeds, InputValidator
from src.db.repository import ContactLogRepository
from src.logging_setup import setup_logging

logger = setup_logging("agent.planner")


class Intent(str, Enum):
    FIND_DOCTOR = "find_doctor"
    CONFIRM_RESERVATION = "confirm_reservation"
    REJECT = "reject"
    PROVIDE_INFO = "provide_info"
    CSAT = "csat"
    ESCALATE = "escalate"
    GREETING = "greeting"
    UNKNOWN = "unknown"


@dataclass
class AgentState:
    contact_id: int | None = None
    needs: ExtractedNeeds = field(default_factory=ExtractedNeeds)
    candidates: list[RankedDoctor] = field(default_factory=list)
    selected: RankedDoctor | None = None
    reserved: bool = False
    escalated: bool = False
    turn: int = 0
    max_turns: int = 12
    history: list[dict[str, str]] = field(default_factory=list)
    done: bool = False
    token_cost: int = 0
    pending_search_confirm: bool = False


class AgentPlanner:
    def __init__(
        self,
        validator: InputValidator,
        search: DoctorSearchTool,
        contacts: ContactLogRepository,
    ):
        self.validator = validator
        self.search = search
        self.contacts = contacts
        self.llm = get_llm()
        self.state = AgentState()

    def start_session(self, phone_number: str | None = None) -> str:
        self.state = AgentState()
        self.state.contact_id = self.contacts.start(phone_number=phone_number)
        return (
            "Hello! I'm the healthcare matching assistant.\n"
            "Please describe what kind of doctor you need — for example:\n"
            "  \"I need a kind female pediatrician in Tokyo available tomorrow\"\n"
            "Type 'quit' to exit, 'escalate' to speak with a human operator."
        )

    def detect_intent(self, text: str) -> Intent:
        lower = text.strip().lower()
        if lower in {"quit", "exit", "bye", "goodbye"}:
            return Intent.REJECT
        if lower in {"escalate", "human", "operator", "agent"}:
            return Intent.ESCALATE
        if lower in {"yes", "y", "confirm", "ok", "okay", "reserve", "book", "sure"}:
            if self.state.candidates or self.state.selected or self.state.pending_search_confirm:
                return Intent.CONFIRM_RESERVATION
        if lower in {"no", "n", "reject", "nope", "not this", "another", "different"}:
            if self.state.candidates or self.state.pending_search_confirm:
                return Intent.REJECT
        if lower.isdigit() and self.state.candidates:
            return Intent.CONFIRM_RESERVATION
        if lower.startswith("csat") or lower.startswith("rating"):
            return Intent.CSAT
        if any(g in lower for g in ("hello", "hi ", "hi,", "hey")):
            if len(lower.split()) <= 3:
                return Intent.GREETING
        return Intent.FIND_DOCTOR if not self.state.candidates else Intent.PROVIDE_INFO

    def extract_needs(self, text: str) -> ExtractedNeeds:
        needs = self.validator.extract_rule_based(text)
        if self.llm.available:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Extract patient doctor preferences as JSON with keys: "
                        "gender, expertise, region, personality, available_datetime, confidence (0-1). "
                        "Use null for unknown. expertise should be a medical specialty name. "
                        "Reply with JSON only."
                    ),
                },
                {"role": "user", "content": text},
            ]
            try:
                raw = self.llm.chat(messages, temperature=0.0, max_tokens=300)
                data = self.llm.extract_json(raw)
                if isinstance(data, dict):
                    needs = self.validator.merge_llm_extraction(needs, data)
            except Exception as exc:
                logger.warning("LLM extraction failed: %s", exc)
        # Merge with previous state (keep prior fields if new message only adds some)
        prev = self.state.needs
        for key in ("gender", "expertise", "region", "personality", "available_datetime"):
            new_val = getattr(needs, key)
            old_val = getattr(prev, key)
            if not new_val and old_val:
                setattr(needs, key, old_val)
        if needs.confidence < prev.confidence and not any(
            getattr(needs, k) for k in ("expertise", "region", "gender")
        ):
            needs.confidence = max(needs.confidence, prev.confidence)
        self.state.needs = needs
        return needs

    def plan_and_search(self) -> list[RankedDoctor]:
        n = self.state.needs
        mode = "hybrid"
        # More verbal / incomplete → lean semantic
        if not n.expertise and not n.region:
            mode = "semantic"
        ranked = self.search.find_doctor(
            expertise=n.expertise,
            region=n.region,
            gender=n.gender,
            personality=n.personality,
            available_datetime=n.available_datetime,
            query=n.to_keywords_str() or None,
            mode=mode,
            limit=5,
        )
        self.state.candidates = ranked
        return ranked

    def llm_rerank_and_respond(self, ranked: list[RankedDoctor]) -> str:
        if not ranked:
            return (
                "I couldn't find a matching doctor with the current criteria.\n"
                "Please provide more details (specialty, region, gender preference), "
                "or type 'escalate' for a human operator."
            )

        if self.llm.available:
            context = doctors_to_llm_context(ranked)
            needs = self.state.needs.as_search_params()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful healthcare call-center assistant. "
                        "Given patient needs and candidate doctors, recommend the best matches. "
                        "Be concise, empathetic, and clear. Do not invent doctors. "
                        "Ask the patient to confirm a number to reserve, or say no for other options."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Patient needs: {json.dumps(needs)}\n"
                        f"Candidates:\n{context}\n\n"
                        "Write a short recommendation for the patient."
                    ),
                },
            ]
            try:
                text = self.llm.chat(messages, temperature=0.3, max_tokens=500)
                self.state.token_cost = self.llm.token_usage
                return text + self._selection_footer(ranked)
            except Exception as exc:
                logger.warning("LLM response failed: %s", exc)

        # Offline fallback response
        lines = [
            "Based on your needs, here are the best matching doctors:",
            f"  Criteria: {self.state.needs.to_keywords_str() or '(general)'}",
            "",
        ]
        for i, r in enumerate(ranked, 1):
            lines.append(f"[{i}]")
            lines.append(r.display())
            lines.append("")
        lines.append("Reply with a number to reserve, 'no' for other options, or 'escalate'.")
        return "\n".join(lines)

    def _selection_footer(self, ranked: list[RankedDoctor]) -> str:
        names = ", ".join(f"{i}={r.doctor.name}" for i, r in enumerate(ranked, 1))
        return f"\n\n(Options: {names}. Reply with a number to reserve, or 'no' / 'escalate'.)"

    def handle(self, user_text: str) -> str:
        self.state.turn += 1
        self.state.history.append({"role": "user", "content": user_text})

        if self.state.turn > self.state.max_turns:
            self.state.escalated = True
            self.state.done = True
            self._finish(is_accurate=0)
            return "We've reached the maximum turns. Escalating to a human operator. Goodbye."

        intent = self.detect_intent(user_text)
        logger.info("Intent=%s turn=%s", intent, self.state.turn)

        if intent == Intent.ESCALATE:
            self.state.escalated = True
            self.state.done = True
            self._finish(is_accurate=0)
            return "Connecting you to a human operator. Please hold."

        if intent == Intent.GREETING:
            return (
                "Hi! Tell me about the doctor you need — specialty/illness, region, "
                "preferred gender, personality, and when you're available."
            )

        if intent == Intent.CSAT:
            return self._handle_csat(user_text)

        if intent == Intent.CONFIRM_RESERVATION:
            return self._handle_confirm(user_text)

        if intent == Intent.REJECT and (self.state.candidates or self.state.pending_search_confirm):
            self.state.candidates = []
            self.state.selected = None
            self.state.pending_search_confirm = False
            return (
                "No problem. Please refine what you're looking for "
                "(e.g. different region, gender, or specialty), or type 'escalate'."
            )

        # FIND_DOCTOR / PROVIDE_INFO
        needs = self.extract_needs(user_text)
        missing = needs.missing_required()

        if missing:
            questions = []
            if "expertise / illness specialty" in missing:
                questions.append(
                    "- What illness or specialty do you need? (e.g. Pediatrics, Cardiology, ENT)"
                )
            if "region" in missing:
                questions.append("- Which region or city?")
            if not needs.gender:
                questions.append("- Any gender preference for the doctor? (optional)")
            if not needs.personality:
                questions.append("- Preferred personality? (kind, professional, humor…) (optional)")
            if not needs.available_datetime:
                questions.append("- When would you like the appointment? (optional)")
            conf = f"{needs.confidence:.0%}"
            return (
                f"I understood (confidence {conf}): "
                f"{needs.to_keywords_str() or 'not much yet'}.\n"
                "I need a bit more information:\n" + "\n".join(questions)
            )

        # Confirm when confidence is below threshold UNLESS core fields are solid
        # (expertise + region present and confidence reasonably high → search directly)
        threshold = self.validator.settings.confidence_threshold
        core_ok = bool(needs.expertise and needs.region and needs.confidence >= 0.85)
        if (
            needs.confidence < threshold
            and needs.expertise
            and not core_ok
            and not self.state.pending_search_confirm
        ):
            self.state.pending_search_confirm = True
            return (
                f"Just to confirm — you need a doctor with:\n"
                f"  Expertise: {needs.expertise}\n"
                f"  Region: {needs.region or '(any)'}\n"
                f"  Gender: {needs.gender or '(any)'}\n"
                f"  Personality: {needs.personality or '(any)'}\n"
                f"  Time: {needs.available_datetime or '(any)'}\n"
                f"(confidence {needs.confidence:.0%})\n"
                "Reply 'yes' to search, or correct any field."
            )

        self.state.pending_search_confirm = False

        if not needs.expertise:
            return (
                "Could you tell me more about the medical issue or specialty? "
                "For example: skin rash → Dermatology, child fever → Pediatrics, ear pain → ENT."
            )

        ranked = self.plan_and_search()
        if not ranked:
            return (
                "No matching doctors found. Try a broader region or specialty, "
                "or type 'escalate' for a human operator."
            )

        return self.llm_rerank_and_respond(ranked)

    def _handle_confirm(self, text: str) -> str:
        lower = text.strip().lower()
        if not self.state.candidates:
            # User confirmed extraction — run search
            if self.state.needs.expertise:
                self.state.pending_search_confirm = False
                ranked = self.plan_and_search()
                return self.llm_rerank_and_respond(ranked)
            return "Please describe the doctor you need first."

        selected: RankedDoctor | None = None
        if lower.isdigit():
            idx = int(lower) - 1
            if 0 <= idx < len(self.state.candidates):
                selected = self.state.candidates[idx]
        else:
            selected = self.state.candidates[0]

        if not selected:
            return "Invalid selection. Please reply with a valid option number."

        self.state.selected = selected
        self.state.reserved = True
        self.state.done = True
        d = selected.doctor
        self._finish(doctor_name=d.name, is_accurate=1)
        slots = ", ".join(d.available_slots[:3]) if d.available_slots else "to be scheduled"
        return (
            f"Reservation confirmed!\n"
            f"  Doctor:    {d.name}\n"
            f"  Expertise: {d.expertise}\n"
            f"  Hospital:  {d.hospital_name}\n"
            f"  Region:    {d.region}\n"
            f"  Slots:     {slots}\n"
            f"You will receive a confirmation SMS shortly.\n"
            f"Optional: rate this session with 'csat 5' (1-5). Type 'quit' to exit."
        )

    def _handle_csat(self, text: str) -> str:
        import re

        m = re.search(r"([1-5](?:\.\d+)?)", text)
        if not m:
            return "Please provide a CSAT score from 1 to 5, e.g. 'csat 4'."
        score = float(m.group(1))
        if self.state.contact_id:
            self.contacts.finish(self.state.contact_id, csat=score)
        return f"Thank you! Recorded CSAT={score}. Type 'quit' to exit."

    def _finish(
        self,
        doctor_name: str | None = None,
        is_accurate: int | None = None,
    ) -> None:
        if self.state.contact_id is None:
            return
        self.contacts.finish(
            self.state.contact_id,
            doctor_name=doctor_name,
            is_accurate=is_accurate,
            token_cost=self.state.token_cost or self.llm.token_usage,
            keywords=self.state.needs.to_keywords_str() or None,
        )

    def end_session(self) -> None:
        if not self.state.done and self.state.contact_id:
            self._finish(
                doctor_name=self.state.selected.doctor.name if self.state.selected else None,
                is_accurate=1 if self.state.reserved else 0,
            )
        self.state.done = True
