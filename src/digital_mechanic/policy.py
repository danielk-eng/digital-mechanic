"""Deterministic policy from the official Q&A (not an LLM decision)."""

from __future__ import annotations

from .schemas import CheckResult, CheckStatus, PolicyDecision, RecordingQuality


def decide(check: CheckResult, *, found_specific: bool, found_general_inspection: bool) -> PolicyDecision:
    """Return the only legal next step for this check and retrieval outcome.

    found_specific: manual of *this* engine has a procedure for the fault.
    found_general_inspection: no specific procedure, but the same manual has
    a general inspection / unit check that can be cited.
    """
    if check.status == CheckStatus.check_failed or check.quality == RecordingQuality.bad:
        return PolicyDecision.repeat_recording
    if check.status == CheckStatus.normal:
        return PolicyDecision.no_ticket_normal
    if check.status != CheckStatus.anomaly:
        return PolicyDecision.repeat_recording
    if found_specific:
        return PolicyDecision.propose_ticket
    if found_general_inspection:
        return PolicyDecision.propose_ticket_general_inspection
    return PolicyDecision.advise_teardown_no_ticket


def may_post_ticket(decision: PolicyDecision) -> bool:
    return decision in {
        PolicyDecision.propose_ticket,
        PolicyDecision.propose_ticket_general_inspection,
    }
