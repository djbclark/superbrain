#!/usr/bin/env python3
"""
Category Manager for SuperBrain (DEPRECATED — MongoDB-era)

This interactive utility talked to a MongoDB-style ``db.collection`` API and is
incompatible with the active SQLite database. Do not use it for taxonomy
migration or category edits.

Use instead:
  - ``python scripts/recategorize.py`` for metadata-only taxonomy migration
  - ``PUT /post/{shortcode}`` / the mobile app for single-post category edits
  - ``config/categories.toml`` for the user-owned taxonomy definition
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel

console = Console()


def main():
    console.print(Panel("Category Manager (DEPRECATED)", style="bold magenta", expand=False))
    console.print(
        "[red]This tool is a stale MongoDB-era utility and must not be used "
        "with the SQLite SuperBrain database.[/red]\n"
        "Use [cyan]python scripts/recategorize.py --help[/cyan] for taxonomy "
        "migration, and edit [cyan]config/categories.toml[/cyan] for the "
        "user-owned category list."
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
