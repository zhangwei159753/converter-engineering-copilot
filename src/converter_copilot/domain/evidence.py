"""Deterministic citation integrity checks for generated reports."""

import re
from collections import Counter

from converter_copilot.domain.schemas import ConverterReport, EvidenceCheckResult


def validate_report_evidence(report: ConverterReport) -> EvidenceCheckResult:
    """Classify report claims using only the evidence links and source statuses."""
    counts = Counter(item.evidence_id for item in report.evidence)
    duplicates = {evidence_id for evidence_id, count in counts.items() if count > 1}
    evidence_by_id = {item.evidence_id: item for item in report.evidence if counts[item.evidence_id] == 1}
    warnings = [f"Duplicate evidence ID '{evidence_id}' appears {counts[evidence_id]} times." for evidence_id in sorted(duplicates)]
    invalid_metadata = set()
    for item in report.evidence:
        if item.source_type == "web" and not item.url:
            invalid_metadata.add(item.evidence_id)
            warnings.append(f"Evidence '{item.evidence_id}' is missing a traceable source URL.")
        elif item.source_type == "local_document" and (not item.document_id or not item.locator):
            invalid_metadata.add(item.evidence_id)
            warnings.append(f"Evidence '{item.evidence_id}' is missing its document ID or locator.")
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
        metadata_missing = sorted({evidence_id for evidence_id in claim.evidence_ids if evidence_id in invalid_metadata})
        if missing:
            warnings.append(f"Claim '{claim.claim_id}' references missing evidence IDs: {', '.join(missing)}.")
        if duplicated:
            warnings.append(f"Claim '{claim.claim_id}' references ambiguous duplicate evidence IDs: {', '.join(duplicated)}.")
        if metadata_missing:
            warnings.append(f"Claim '{claim.claim_id}' references evidence with missing source metadata: {', '.join(metadata_missing)}.")
        if missing or duplicated or metadata_missing:
            unverified.append(claim.claim_id)
            continue

        sources = [evidence_by_id[evidence_id] for evidence_id in claim.evidence_ids]
        if any(source.applicability in {"related", "cross_equipment"} for source in sources):
            unverified.append(claim.claim_id)
            warnings.append(
                f"Claim '{claim.claim_id}' cites related or cross-equipment material that cannot directly support a target-equipment cause."
            )
            continue
        if _causality_exceeds_source(claim.text, sources):
            unverified.append(claim.claim_id)
            warnings.append(
                f"Claim '{claim.claim_id}' makes a definitive causal assertion stronger than its tentative source wording."
            )
            continue
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


_DEFINITIVE_CAUSALITY = re.compile(r"导致|引起|造成|誘发|诱发|根因是|原因是|caused by|causes|results in", re.IGNORECASE)
_TENTATIVE_SOURCE = re.compile(r"假设|可能相关|可能导致|需核对|需要核对|尚未验证|不是现场验证结论|不代表真实|may be related|hypothesis|not validated", re.IGNORECASE)


def _causality_exceeds_source(claim_text: str, sources) -> bool:
    """Reject definitive cause language when the cited material explicitly remains tentative."""
    return bool(_DEFINITIVE_CAUSALITY.search(claim_text)) and any(
        _TENTATIVE_SOURCE.search(source.excerpt) for source in sources
    )
