from digital_mechanic import policy
from digital_mechanic.schemas import CheckResult, PolicyDecision, Urgency


def chk(**kw):
    base = dict(vehicle_id="V", session_id="S-1", status="anomaly", anomaly_type="F08", anomaly_title="t", anomaly_score=0.76,
                confidence=0.73, signal_quality="good", description="d", model_version="mock-v1")
    base.update(kw)
    return CheckResult(**base)


def test_check_failed_means_repeat_and_no_ticket():
    c = chk(status="check_failed", anomaly_type=None, anomaly_title=None, anomaly_score=None, confidence=None, signal_quality="bad")
    assert policy.decide(c) == PolicyDecision.repeat_recording
    assert not policy.may_create_ticket(c)


def test_normal_no_ticket():
    c = chk(status="normal", anomaly_type=None, anomaly_title=None, anomaly_score=0.1)
    assert policy.decide(c) == PolicyDecision.no_ticket_normal
    assert not policy.may_create_ticket(c)
    assert policy.fleet_status(c) == "ready"


def test_low_confidence_inspect_first():
    c = chk(confidence=0.56)
    assert policy.decide(c) == PolicyDecision.verify_then_ticket
    assert policy.may_create_ticket(c)
    assert any("уверенность" in f for f in policy.doubt_flags(c))


def test_urgency_engine_critical_high():
    u, why = policy.urgency(chk(anomaly_type="F02", anomaly_score=0.85, confidence=0.9))
    assert u == Urgency.high and why
    assert policy.fleet_status(chk(anomaly_type="F02", anomaly_score=0.85, confidence=0.9)) == "quarantine"


def test_urgency_aux_low_score_low():
    u, _ = policy.urgency(chk(anomaly_type="F05", anomaly_score=0.55, confidence=0.55, signal_quality="warning"))
    assert u == Urgency.low


def test_fleet_distribution_reasonable():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data/official/vehicles.json").read_text(encoding="utf-8"))
    buckets = {"ready": 0, "attention": 0, "quarantine": 0}
    for v in data["vehicles"]:
        buckets[policy.fleet_status(CheckResult(**v["sessions"][-1]))] += 1
    assert sum(buckets.values()) == 50 and buckets["quarantine"] >= 1 and buckets["ready"] >= 20
