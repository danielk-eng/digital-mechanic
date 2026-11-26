from digital_mechanic.policy import decide, may_post_ticket
from digital_mechanic.schemas import CheckResult, CheckStatus, PolicyDecision, RecordingQuality


def _check(**kwargs) -> CheckResult:
    base = dict(vehicle_id="A123BC77", session_id="SES-0001", status=CheckStatus.anomaly)
    base.update(kwargs)
    return CheckResult(**base)


def test_failed_check_asks_to_rerecord():
    d = decide(_check(status=CheckStatus.check_failed), found_specific=True, found_general_inspection=True)
    assert d == PolicyDecision.repeat_recording
    assert not may_post_ticket(d)


def test_bad_quality_asks_to_rerecord_even_if_manual_has_a_procedure():
    d = decide(
        _check(status=CheckStatus.anomaly, quality=RecordingQuality.bad),
        found_specific=True,
        found_general_inspection=False,
    )
    assert d == PolicyDecision.repeat_recording
    assert not may_post_ticket(d)


def test_normal_has_no_ticket():
    d = decide(_check(status=CheckStatus.normal), found_specific=True, found_general_inspection=False)
    assert d == PolicyDecision.no_ticket_normal
    assert not may_post_ticket(d)


def test_anomaly_with_specific_procedure_proposes_ticket():
    d = decide(_check(), found_specific=True, found_general_inspection=False)
    assert d == PolicyDecision.propose_ticket
    assert may_post_ticket(d)


def test_anomaly_with_only_general_inspection_may_cite_it():
    d = decide(_check(), found_specific=False, found_general_inspection=True)
    assert d == PolicyDecision.propose_ticket_general_inspection
    assert may_post_ticket(d)


def test_anomaly_without_any_citation_does_not_post():
    d = decide(_check(), found_specific=False, found_general_inspection=False)
    assert d == PolicyDecision.advise_teardown_no_ticket
    assert not may_post_ticket(d)
