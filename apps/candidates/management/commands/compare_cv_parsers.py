"""Compare the regex parser with Claude on a folder of real CVs.

uv run python manage.py compare_cv_parsers ~/cv-samples --out cv-compare.csv
"""

import csv
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.candidates.cv_fields import normalize_fields
from apps.candidates.llm_parsing import llm_extract
from apps.candidates.parsing import extract_text, guess_fields

SUFFIXES = {".pdf", ".docx"}


class Command(BaseCommand):
    help = "Write a CSV comparing regex and Claude CV parsing, one row per file and field."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("folder", type=Path)
        parser.add_argument("--out", type=Path, default=Path("cv-compare.csv"))

    def handle(self, *args: Any, **options: Any) -> None:
        if not (settings.ANTHROPIC_API_KEY and settings.CV_PARSE_MODEL):
            raise CommandError("Set ANTHROPIC_API_KEY and CV_PARSE_MODEL in .env first.")
        folder = Path(options["folder"])
        if not folder.is_dir():
            raise CommandError(f"{folder} is not a folder.")
        paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in SUFFIXES)
        out = Path(options["out"])
        with out.open("w", newline="", encoding="utf-8") as report:
            writer = csv.writer(report)
            writer.writerow(["file", "field", "regex", "claude"])
            for path in paths:
                with path.open("rb") as handle:
                    text = extract_text(File(handle, name=path.name))
                    data = handle.read()
                regex = normalize_fields(guess_fields(text), source_text=text)
                read = llm_extract(data, path.name, text)
                raw = read.fields if read else None
                claude = normalize_fields(raw, source_text=text) if raw else {}
                for field, value in regex.items():
                    writer.writerow([path.name, field, value, claude.get(field, "")])
        self.stdout.write(f"Compared {len(paths)} CVs into {out}")
