"""
Config-driven content classification.

Uses ModelRouter text generation with a JSON schema contract, then validates
locally against the effective taxonomy. Never invents categories, never adopts
out-of-taxonomy suggestions automatically, and never falls back to keyword lists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.taxonomy import TaxonomyConfig, get_taxonomy


@dataclass
class ClassificationResult:
    category: str
    confidence: Optional[float]
    rationale: str
    suggestions: list[str] = field(default_factory=list)
    source: str = "model"  # model | fallback | migration | manual
    fallback_reason: Optional[str] = None
    raw: Optional[dict[str, Any]] = None

    def as_db_fields(self, taxonomy_version: str) -> dict[str, Any]:
        return {
            "category": self.category,
            "category_source": self.source,
            "category_confidence": self.confidence,
            "category_rationale": (self.rationale or "")[:2000],
            "category_suggestions_json": json.dumps(self.suggestions, ensure_ascii=False),
            "category_taxonomy_version": taxonomy_version,
        }


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    # Strip common markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_classification_prompt(
    taxonomy: TaxonomyConfig,
    *,
    title: str = "",
    summary: str = "",
    tags: Optional[list | str] = None,
    extra_text: str = "",
) -> str:
    if isinstance(tags, list):
        tags_s = " ".join(str(t) for t in tags)
    else:
        tags_s = str(tags or "")

    multi = taxonomy.allow_multiple_categories
    primary_rule = (
        "primary_category must be exactly one configured category name."
        if not multi
        else (
            "primary_category must be exactly one configured category name "
            "(the best single match). additional_categories may list other "
            "configured matches; the server will apply precedence if single-mode."
        )
    )

    return f"""You are classifying saved content into a fixed user taxonomy.

Effective taxonomy (lower precedence number wins on ties):
{taxonomy.category_prompt_block()}

Rules:
- Classify from evidence in the provided title, summary, tags, and excerpt only.
- {primary_rule}
- Prefer a specific category over {taxonomy.fallback_category}; use {taxonomy.fallback_category} only when nothing else fits.
- Pay special attention to guidance text that distinguishes overlapping categories.
- Before stating confidence, briefly consider the next-best alternative category.
- confidence is a number from 0.0 to 1.0 reflecting how sure you are about primary_category.
- out_of_taxonomy_suggestions: zero or more short labels for themes that are NOT in the taxonomy. Never invent taxonomy entries. Do not list configured category names here.
- Respond with ONLY a JSON object (no markdown), using this shape:
{{
  "primary_category": "<exact taxonomy name>",
  "additional_categories": [],
  "confidence": 0.0,
  "rationale": "<one short sentence citing evidence>",
  "out_of_taxonomy_suggestions": []
}}

TITLE: {title}

SUMMARY: {summary}

TAGS: {tags_s}

EXCERPT:
{(extra_text or '')[:4000]}
"""


def validate_classification(
    data: Optional[dict],
    taxonomy: TaxonomyConfig,
    *,
    source: str = "model",
) -> ClassificationResult:
    """Validate model JSON and enforce taxonomy invariants."""
    if not data:
        return ClassificationResult(
            category=taxonomy.fallback_category,
            confidence=None,
            rationale="",
            suggestions=[],
            source="fallback",
            fallback_reason="malformed_or_empty_model_output",
            raw=data if isinstance(data, dict) else None,
        )

    primary_raw = data.get("primary_category") or data.get("category") or ""
    additional = data.get("additional_categories") or []
    if not isinstance(additional, list):
        additional = []

    candidates = [str(primary_raw)] + [str(x) for x in additional]
    if taxonomy.allow_multiple_categories:
        # Still store a single primary for analyses.category compatibility:
        # highest-precedence (lowest number) among valid matches.
        chosen = taxonomy.pick_single(candidates)
    else:
        # Single-category mode: user categories already outrank defaults in the
        # effective taxonomy; resolve ambiguity strictly by precedence.
        chosen = taxonomy.pick_single(candidates)

    suggestions_raw = data.get("out_of_taxonomy_suggestions") or data.get("suggestions") or []
    if not isinstance(suggestions_raw, list):
        suggestions_raw = []
    suggestions: list[str] = []
    for s in suggestions_raw:
        label = str(s).strip()
        if not label:
            continue
        if taxonomy.resolve_name(label):
            continue  # never treat configured names as "outside" suggestions
        # normalize lightly
        norm = re.sub(r"\s+", " ", label)
        if norm.lower() not in {x.lower() for x in suggestions}:
            suggestions.append(norm[:80])

    rationale = str(data.get("rationale") or "").strip()
    confidence = data.get("confidence")
    try:
        confidence_f = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_f = None
    if confidence_f is not None:
        confidence_f = max(0.0, min(1.0, confidence_f))

    if not chosen:
        return ClassificationResult(
            category=taxonomy.fallback_category,
            confidence=confidence_f,
            rationale=rationale,
            suggestions=suggestions,
            source="fallback",
            fallback_reason="primary_not_in_taxonomy",
            raw=data,
        )

    if confidence_f is not None and confidence_f < taxonomy.confidence_threshold:
        return ClassificationResult(
            category=taxonomy.fallback_category,
            confidence=confidence_f,
            rationale=rationale,
            suggestions=suggestions,
            source="fallback",
            fallback_reason="low_confidence",
            raw=data,
        )

    return ClassificationResult(
        category=chosen,
        confidence=confidence_f,
        rationale=rationale,
        suggestions=suggestions,
        source=source,
        fallback_reason=None,
        raw=data,
    )


def classify_content(
    *,
    title: str = "",
    summary: str = "",
    tags: Optional[list | str] = None,
    extra_text: str = "",
    taxonomy: Optional[TaxonomyConfig] = None,
    router=None,
    source: str = "model",
) -> ClassificationResult:
    """
    Classify content with the model router and validate against taxonomy.

    On router failure or unparseable output, returns fallback_category with a reason.
    """
    tax = taxonomy or get_taxonomy()
    prompt = build_classification_prompt(
        tax,
        title=title,
        summary=summary,
        tags=tags,
        extra_text=extra_text,
    )

    raw_text = ""
    try:
        if router is None:
            from core.model_router import get_router

            router = get_router()
        raw_text = router.generate_text(prompt) or ""
    except Exception as exc:
        return ClassificationResult(
            category=tax.fallback_category,
            confidence=None,
            rationale="",
            suggestions=[],
            source="fallback",
            fallback_reason=f"model_error:{type(exc).__name__}",
            raw=None,
        )

    data = _extract_json_object(raw_text)
    if data is None:
        # One repair retry asking only for JSON
        try:
            repair = (
                "Convert the following into the required classification JSON object only.\n\n"
                + raw_text[:3000]
            )
            raw_text2 = router.generate_text(repair) or ""
            data = _extract_json_object(raw_text2)
        except Exception:
            data = None

    return validate_classification(data, tax, source=source)


def normalize_suggestion_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip()).lower()


def aggregate_suggestions(
    suggestion_lists: list[list[str]],
    taxonomy: TaxonomyConfig,
    *,
    min_count: Optional[int] = None,
) -> list[dict]:
    """Aggregate out-of-taxonomy suggestions for an advisory batch report."""
    threshold = min_count if min_count is not None else taxonomy.suggestion_min_count
    counts: dict[str, dict] = {}
    for suggestions in suggestion_lists:
        for label in suggestions:
            if taxonomy.resolve_name(label):
                continue
            key = normalize_suggestion_label(label)
            if not key:
                continue
            entry = counts.setdefault(
                key, {"label": label.strip(), "count": 0, "examples": []}
            )
            entry["count"] += 1
            if len(entry["examples"]) < 3 and label.strip() not in entry["examples"]:
                entry["examples"].append(label.strip())

    ranked = sorted(counts.values(), key=lambda e: (-e["count"], e["label"].lower()))
    return [e for e in ranked if e["count"] >= threshold]
