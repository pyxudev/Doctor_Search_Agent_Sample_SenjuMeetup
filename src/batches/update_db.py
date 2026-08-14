"""
Auto-updating database batch.

Watches a directory for new/updated JSON files and re-runs ingestion.
Can also run a one-shot scan of pending files.

Usage:
  # Watch mode (used by docker compose service `db-updater`)
  python -m src.batches.update_db --watch --dir data/json/incoming

  # One-shot process all pending JSON in directory
  python -m src.batches.update_db --dir data/json/incoming --once
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.batches.ingest_json import ingest_file
from src.config import get_settings
from src.db.connection import init_db
from src.logging_setup import setup_logging

logger = setup_logging("batches.update_db")

SUPPORTED_SUFFIXES = {".json"}


def process_file(path: Path, *, init: bool = False) -> dict:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        logger.debug("Skipping non-JSON: %s", path)
        return {"status": "skipped", "path": str(path)}
    if not path.is_file():
        return {"status": "skipped", "path": str(path)}

    # Wait briefly for writers to finish (common with docker volume mounts)
    last_size = -1
    for _ in range(10):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return {"status": "missing", "path": str(path)}
        if size == last_size and size > 0:
            break
        last_size = size
        time.sleep(0.2)

    try:
        stats = ingest_file(path, init=init, move_to_processed=True, operator_name="update_batch")
        result = {"status": "ok", "path": str(path), "stats": stats}
        logger.info("Updated DB from %s → %s", path, stats)
        return result
    except Exception as exc:
        logger.exception("Failed to process %s: %s", path, exc)
        return {"status": "error", "path": str(path), "error": str(exc)}


def scan_once(directory: Path, *, init: bool = False) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(directory.glob("*.json")):
        results.append(process_file(path, init=init))
    return results


class JsonIngestHandler(FileSystemEventHandler):
    def __init__(self, debounce_sec: float = 1.0):
        super().__init__()
        self.debounce_sec = debounce_sec
        self._last: dict[str, float] = {}

    def _should_handle(self, path: Path) -> bool:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return False
        # Ignore files already under processed
        if "processed" in path.parts:
            return False
        key = str(path.resolve()) if path.exists() else str(path)
        now = time.time()
        prev = self._last.get(key, 0)
        if now - prev < self.debounce_sec:
            return False
        self._last[key] = now
        return True

    def on_created(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_handle(path):
            logger.info("Detected new file: %s", path)
            process_file(path)

    def on_modified(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._should_handle(path):
            logger.info("Detected updated file: %s", path)
            process_file(path)

    def on_moved(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if self._should_handle(path):
            logger.info("Detected moved-in file: %s", path)
            process_file(path)


def watch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    init_db()
    # Process any files already waiting
    scan_once(directory)

    handler = JsonIngestHandler()
    observer = Observer()
    observer.schedule(handler, str(directory), recursive=False)
    observer.start()
    logger.info("Watching for JSON updates in %s (Ctrl+C to stop)", directory)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping watcher...")
        observer.stop()
    observer.join()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Auto-update DB when JSON data changes")
    parser.add_argument(
        "--dir",
        "-d",
        default=settings.json_watch_dir,
        help="Directory to watch / scan for JSON (default: JSON_WATCH_DIR)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously watch directory for new/updated JSON",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process existing JSON files once and exit",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize schema before processing",
    )
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if args.init_db or args.watch:
        init_db()

    if args.watch:
        watch(directory)
        return 0

    # Default: one-shot if --once or if neither flag (safe for scripts)
    results = scan_once(directory, init=args.init_db)
    print(json.dumps({"status": "ok", "results": results}, indent=2))
    errors = sum(1 for r in results if r.get("status") == "error")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
