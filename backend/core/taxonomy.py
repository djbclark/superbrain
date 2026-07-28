"""
Config-driven category taxonomy for SuperBrain.

Categories and classification guidance live in user configuration
(`config/categories.toml`), not in application prompts hard-coded in source.
Check in only `categories.toml.example`. The real local file is gitignored and
must not be overwritten by deploy-local.sh.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "SUPERBRAIN_CATEGORIES_CONFIG",
        str(CONFIG_DIR / "categories.toml"),
    )
)
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "categories.toml.example"

# Built-in defaults used only when use_default_categories is true, or when no
# config file exists (legacy install compatibility).
BUILTIN_DEFAULT_CATEGORIES: tuple[tuple[str, int, str], ...] = (
    ("product", 100, "Physical products, gadgets, and reviews."),
    ("places", 110, "Travel, destinations, locations, and itineraries."),
    ("food", 120, "Food, cooking, restaurants, and recipes."),
    ("software", 130, "Apps, software products, and programming topics."),
    ("book", 140, "Books, authors, and literature."),
    ("tv shows", 150, "Television series and streaming shows."),
    ("fitness", 160, "Workouts, fitness, and exercise."),
    ("film", 170, "Movies and cinema."),
    ("event", 180, "Events, conferences, festivals, and meetups."),
    ("other", 999, "Content that does not fit another category."),
)

DEFAULT_CONFIDENCE_THRESHOLD = 0.55
DEFAULT_SUGGESTION_MIN_COUNT = 5


@dataclass(frozen=True)
class CategoryDef:
    name: str
    precedence: int
    guidance: str
    source: str  # "user" | "default"


@dataclass(frozen=True)
class TaxonomyConfig:
    use_default_categories: bool
    allow_multiple_categories: bool
    fallback_category: str
    confidence_threshold: float
    suggestion_min_count: int
    categories: tuple[CategoryDef, ...]
    config_path: Optional[Path] = None
    loaded_from_file: bool = False

    @property
    def by_name_lower(self) -> dict[str, CategoryDef]:
        return {c.name.lower(): c for c in self.categories}

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.categories]

    @property
    def version(self) -> str:
        """Stable short hash of the effective taxonomy (names + precedence + guidance)."""
        blob = "|".join(
            f"{c.name}\0{c.precedence}\0{c.guidance}\0{c.source}"
            for c in sorted(self.categories, key=lambda x: (x.precedence, x.name.lower()))
        )
        blob += (
            f"|multi={self.allow_multiple_categories}"
            f"|fallback={self.fallback_category}"
            f"|thresh={self.confidence_threshold}"
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def resolve_name(self, raw: str) -> Optional[str]:
        """Map a free-form label onto a configured category name (case-insensitive)."""
        if not raw:
            return None
        key = raw.strip().lower()
        found = self.by_name_lower.get(key)
        return found.name if found else None

    def pick_single(self, candidates: Iterable[str]) -> Optional[str]:
        """
        Pick exactly one category from candidates using configured precedence.
        Lower precedence number wins. Unknown names are ignored.
        """
        resolved: list[CategoryDef] = []
        for raw in candidates:
            name = self.resolve_name(raw)
            if not name:
                continue
            resolved.append(self.by_name_lower[name.lower()])
        if not resolved:
            return None
        best = min(resolved, key=lambda c: (c.precedence, c.name.lower()))
        return best.name

    def category_prompt_block(self) -> str:
        lines = []
        for cat in sorted(self.categories, key=lambda c: c.precedence):
            lines.append(f"- {cat.name} (precedence {cat.precedence}): {cat.guidance}")
        return "\n".join(lines)

    def category_choice_line(self) -> str:
        names = ", ".join(self.names)
        if self.allow_multiple_categories:
            return f"Choose one or more from: {names}"
        return f"Choose exactly ONE from: {names}"


class TaxonomyError(ValueError):
    """Invalid taxonomy configuration."""


def _legacy_default_config() -> TaxonomyConfig:
    cats = tuple(
        CategoryDef(name=n, precedence=p, guidance=g, source="default")
        for n, p, g in BUILTIN_DEFAULT_CATEGORIES
    )
    return TaxonomyConfig(
        use_default_categories=True,
        allow_multiple_categories=False,
        fallback_category="other",
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
        suggestion_min_count=DEFAULT_SUGGESTION_MIN_COUNT,
        categories=cats,
        config_path=None,
        loaded_from_file=False,
    )


def _parse_category_rows(
    rows: list,
    *,
    source: str,
) -> list[CategoryDef]:
    out: list[CategoryDef] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TaxonomyError(f"categories[{i}] must be a table/object")
        name = str(row.get("name", "")).strip()
        if not name:
            raise TaxonomyError(f"categories[{i}].name is required")
        try:
            precedence = int(row.get("precedence"))
        except (TypeError, ValueError) as exc:
            raise TaxonomyError(
                f"categories[{i}].precedence must be a positive integer"
            ) from exc
        if precedence < 1:
            raise TaxonomyError(
                f"categories[{i}].precedence must be >= 1 (got {precedence})"
            )
        guidance = str(row.get("guidance", "")).strip()
        if not guidance:
            raise TaxonomyError(f"categories[{i}].guidance is required")
        out.append(
            CategoryDef(
                name=name,
                precedence=precedence,
                guidance=guidance,
                source=source,
            )
        )
    return out


def build_effective_taxonomy(
    *,
    use_default_categories: bool,
    allow_multiple_categories: bool,
    fallback_category: str,
    confidence_threshold: float,
    suggestion_min_count: int,
    user_categories: list[CategoryDef],
    config_path: Optional[Path],
    loaded_from_file: bool,
) -> TaxonomyConfig:
    """Merge user + optional default categories with single-mode precedence rules."""
    merged: dict[str, CategoryDef] = {}

    if use_default_categories:
        for n, p, g in BUILTIN_DEFAULT_CATEGORIES:
            merged[n.lower()] = CategoryDef(
                name=n, precedence=p, guidance=g, source="default"
            )

    for cat in user_categories:
        key = cat.name.lower()
        if key in merged and merged[key].source == "default":
            # User category wins over default with the same name (single-mode rule).
            merged[key] = cat
        elif key in merged and merged[key].source == "user":
            raise TaxonomyError(f"Duplicate category name: {cat.name}")
        else:
            merged[key] = cat

    categories = tuple(sorted(merged.values(), key=lambda c: (c.precedence, c.name.lower())))
    if not categories:
        raise TaxonomyError("Effective taxonomy is empty")

    # Unique precedence among effective categories
    seen_prec: set[int] = set()
    for cat in categories:
        if cat.precedence in seen_prec:
            raise TaxonomyError(
                f"Duplicate precedence {cat.precedence} in effective taxonomy"
            )
        seen_prec.add(cat.precedence)

    fallback = fallback_category.strip()
    if not any(c.name.lower() == fallback.lower() for c in categories):
        raise TaxonomyError(
            f"fallback_category {fallback!r} is not in the effective taxonomy"
        )
    # Normalize fallback spelling to the configured name
    fallback_name = next(
        c.name for c in categories if c.name.lower() == fallback.lower()
    )

    if confidence_threshold < 0 or confidence_threshold > 1:
        raise TaxonomyError("confidence_threshold must be between 0 and 1")
    if suggestion_min_count < 1:
        raise TaxonomyError("suggestion_min_count must be >= 1")

    return TaxonomyConfig(
        use_default_categories=use_default_categories,
        allow_multiple_categories=allow_multiple_categories,
        fallback_category=fallback_name,
        confidence_threshold=float(confidence_threshold),
        suggestion_min_count=int(suggestion_min_count),
        categories=categories,
        config_path=config_path,
        loaded_from_file=loaded_from_file,
    )


def load_taxonomy(path: Optional[Path] = None) -> TaxonomyConfig:
    """
    Load and validate taxonomy configuration.

    If the config file is missing, returns the built-in legacy default taxonomy
    so existing installs keep working until the operator adds categories.toml.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        print(
            f"[taxonomy] No config at {config_path}; using built-in default categories. "
            f"Copy {EXAMPLE_CONFIG_PATH.name} to categories.toml to customize."
        )
        return _legacy_default_config()

    raw_bytes = config_path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise TaxonomyError(f"Failed to parse {config_path}: {exc}") from exc

    taxonomy = data.get("taxonomy") or {}
    if not isinstance(taxonomy, dict):
        raise TaxonomyError("[taxonomy] section must be a table")

    use_defaults = bool(taxonomy.get("use_default_categories", False))
    allow_multiple = bool(taxonomy.get("allow_multiple_categories", False))
    fallback = str(taxonomy.get("fallback_category", "Other")).strip()
    confidence = float(
        taxonomy.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    )
    suggestion_min = int(
        taxonomy.get("suggestion_min_count", DEFAULT_SUGGESTION_MIN_COUNT)
    )

    user_rows = data.get("categories") or []
    if not isinstance(user_rows, list):
        raise TaxonomyError("categories must be an array of tables")
    user_cats = _parse_category_rows(user_rows, source="user")

    # Detect duplicate user names before merge
    seen_names: set[str] = set()
    for cat in user_cats:
        key = cat.name.lower()
        if key in seen_names:
            raise TaxonomyError(f"Duplicate category name: {cat.name}")
        seen_names.add(key)
    seen_user_prec: set[int] = set()
    for cat in user_cats:
        if cat.precedence in seen_user_prec:
            raise TaxonomyError(f"Duplicate user precedence: {cat.precedence}")
        seen_user_prec.add(cat.precedence)

    return build_effective_taxonomy(
        use_default_categories=use_defaults,
        allow_multiple_categories=allow_multiple,
        fallback_category=fallback,
        confidence_threshold=confidence,
        suggestion_min_count=suggestion_min,
        user_categories=user_cats,
        config_path=config_path,
        loaded_from_file=True,
    )


# Process-level cache; call clear_taxonomy_cache() in tests or after config edits.
_cached: Optional[TaxonomyConfig] = None


def get_taxonomy(path: Optional[Path] = None, *, force_reload: bool = False) -> TaxonomyConfig:
    global _cached
    if path is not None:
        return load_taxonomy(path)
    if force_reload or _cached is None:
        _cached = load_taxonomy(None)
    return _cached


def clear_taxonomy_cache() -> None:
    global _cached
    _cached = None
