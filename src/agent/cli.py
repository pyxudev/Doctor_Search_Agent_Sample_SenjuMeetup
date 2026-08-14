"""
Healthcare call center AI agent — interactive CLI.

Run:
  python -m src.agent.cli
  docker compose up agent
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from src.agent.planner import AgentPlanner
from src.agent.tools import DoctorSearchTool
from src.agent.validation import InputValidator
from src.config import get_settings
from src.db.connection import db_session, init_db
from src.db.repository import ContactLogRepository, DoctorRepository, KeywordRepository
from src.logging_setup import setup_logging

console = Console()
logger = setup_logging("agent.cli")


def ensure_data_ready() -> None:
    """Initialize DB; ingest sample data if empty."""
    settings = get_settings()
    init_db()
    with db_session() as conn:
        count = DoctorRepository(conn).count()
    if count == 0:
        sample = Path(settings.sample_data_path)
        if not sample.exists():
            # Try relative to project root
            sample = Path(__file__).resolve().parents[2] / "data" / "json" / "sample_doctors.json"
        if sample.exists():
            console.print(f"[yellow]Database empty — ingesting sample data from {sample}...[/yellow]")
            from src.batches.ingest_json import ingest_file

            ingest_file(sample, init=False)
        else:
            console.print(
                "[red]No doctors in DB and sample file missing. "
                "Run: python -m src.batches.ingest_json --source data/json/sample_doctors.json --init-db[/red]"
            )


def run_interactive(phone: str | None = None) -> int:
    settings = get_settings()
    ensure_data_ready()

    with db_session() as conn:
        doctors = DoctorRepository(conn)
        keywords = KeywordRepository(conn)
        contacts = ContactLogRepository(conn)
        validator = InputValidator(keywords)
        search = DoctorSearchTool(doctors, keywords)
        agent = AgentPlanner(validator, search, contacts)

        greeting = agent.start_session(phone_number=phone)
        console.print(
            Panel.fit(
                "[bold cyan]Health Care Call Center AI Agent[/bold cyan]\n"
                f"LLM: {'SpaceXAI/' + settings.llm_model if settings.has_llm else 'offline (rule-based)'}\n"
                f"Doctors in DB: {doctors.count()}",
                border_style="cyan",
            )
        )
        console.print(f"[green]Agent:[/green] {greeting}\n")

        while True:
            try:
                user_text = Prompt.ask("[bold blue]You[/bold blue]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[yellow]Session interrupted.[/yellow]")
                agent.end_session()
                conn.commit()
                return 0

            if not user_text.strip():
                continue

            if user_text.strip().lower() in {"quit", "exit", "bye"}:
                agent.end_session()
                conn.commit()
                console.print("[green]Agent:[/green] Thank you for contacting us. Goodbye!")
                return 0

            try:
                reply = agent.handle(user_text.strip())
                conn.commit()  # persist contact log updates mid-session
            except Exception as exc:
                logger.exception("Agent error: %s", exc)
                reply = (
                    "Sorry, something went wrong while processing your request. "
                    "Please try again or type 'escalate'."
                )

            console.print(f"[green]Agent:[/green] {reply}\n")

            if agent.state.done and agent.state.reserved:
                # Allow optional CSAT then exit on next quit
                pass
            if agent.state.escalated:
                conn.commit()
                return 0

    return 0


def run_once(message: str, phone: str | None = None) -> int:
    """Single-shot mode for scripting / tests."""
    ensure_data_ready()
    with db_session() as conn:
        doctors = DoctorRepository(conn)
        keywords = KeywordRepository(conn)
        contacts = ContactLogRepository(conn)
        agent = AgentPlanner(
            InputValidator(keywords),
            DoctorSearchTool(doctors, keywords),
            contacts,
        )
        agent.start_session(phone_number=phone)
        reply = agent.handle(message)
        agent.end_session()
        conn.commit()
        console.print(reply)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Healthcare call center AI agent CLI")
    parser.add_argument("--phone", default=None, help="Patient phone number for contact log")
    parser.add_argument(
        "--message",
        "-m",
        default=None,
        help="Single message mode (non-interactive)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize DB and exit",
    )
    args = parser.parse_args(argv)

    if args.init_db:
        path = init_db()
        console.print(f"Database ready: {path}")
        return 0

    if args.message:
        return run_once(args.message, phone=args.phone)
    return run_interactive(phone=args.phone)


if __name__ == "__main__":
    sys.exit(main())
