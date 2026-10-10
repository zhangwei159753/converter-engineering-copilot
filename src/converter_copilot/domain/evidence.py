"""Deterministic citation integrity checks for generated reports."""

from collections import Counter

from converter_copilot.domain.schemas import ConverterReport, EvidenceCheckResult


def validate_report_evidence(report: ConverterReport) -> EvidenceCheckResult:
    """Classify report claims using only the evidence links and source statuses."""
    counts = Counter(item.evidence_id for item in report.evidence)
    duplicates = {evidence_id for evidence_id, count in counts.items() if count > 1}
    evidence_by_id = {item.evidence_id: item for item in report.evidence if counts[item.evidence_id] == 1}
    warnings = [f"Duplicate evidence ID '{evidence_id}' appears {counts[evidence_id]} times." for evidence_id in sorted(duplicates)]
    supported: list[str] = []
    conflicting: list[str] = []
    unverified: list[str] = []

    for claim in report.claims:
        if not claim.evidence_ids:
            unverified.append(claim.claim_id)
            warnings.append(f"Claim '{claim.claim_id}' has no evidence citations.")
            continue

        missing = sorted({evidence_id for evidence_id in claim.evidence_ids if evidence_id not in counts})
        duplicated = sorted({evidence_id for evidence_id in claim.evidence_ids if evidence_id in duplicates})
        if missing:
            warnings.append(f"Claim '{claim.claim_id}' references missing evidence IDs: {', '.join(missing)}.")
        if duplicated:
            warnings.append(f"Claim '{claim.claim_id}' references ambiguous duplicate evidence IDs: {', '.join(duplicated)}.")
        if missing or duplicated:
            unverified.append(claim.claim_id)
            continue

        sources = [evidence_by_id[evidence_id] for evidence_id in claim.evidence_ids]
        if any(source.source_status == "conflicting" for source in sources):
            conflicting.append(claim.claim_id)
        elif all(source.source_status == "available" for source in sources):
            supported.append(claim.claim_id)
        else:
            unverified.append(claim.claim_id)

    return EvidenceCheckResult(
        supported_claim_ids=supported,
        conflicting_claim_ids=conflicting,
        unverified_claim_ids=unverified,
        warnings=warnings,
    )
