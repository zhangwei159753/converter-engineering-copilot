"""Deterministic equipment applicability labels for retrieved engineering evidence."""

import re
import unicodedata

Applicability = str


def classify_equipment_applicability(target: str | None, source: str | None) -> Applicability:
    """Classify source equipment against the requested equipment without treating similarity as identity."""
    if not target or not source or not target.strip() or not source.strip():
        return "unknown"

    normalized_target = _normalize(target)
    normalized_source = _normalize(source)
    if normalized_target == normalized_source:
        return "direct"

    target_model = _model_family(target)
    source_model = _model_family(source)
    if target_model or source_model:
        if target_model and source_model:
            return "related" if target_model == source_model else "cross_equipment"
        return "cross_equipment"

    target_family = _equipment_family(target)
    source_family = _equipment_family(source)
    if target_family and source_family:
        return "related" if target_family == source_family else "cross_equipment"
    if "变流器" in target and "变流器" in source:
        return "related"
    return "unknown"


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s/\\_-]+", "", normalized)


def _model_family(value: str) -> str | None:
    match = re.search(r"\b([a-z]{2,})[-_/ ]?\d+\b", value, re.IGNORECASE)
    return match.group(1).casefold() if match else None


def _equipment_family(value: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    families = (
        ("storage", ("储能", "bess")),
        ("photovoltaic", ("光伏", "pv inverter", "pv逆变器")),
        ("wind", ("风电", "wind converter")),
        ("ups", ("ups", "不间断电源")),
        ("industrial_drive", ("工业变频驱动", "工业变频器", "drive")),
        ("converter", ("变流器", "converter")),
    )
    for family, markers in families:
        if any(marker in normalized for marker in markers):
            return family
    return None
