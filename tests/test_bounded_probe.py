import os
import json
import pytest
from scripts.run_azure_bounded_probe import validate_max_records, run_bounded_probe

def test_negative_and_zero_max_records_rejected():
    """
    Reviewer Point 2: Negative and zero limits must be strictly rejected
    rather than allowing negative slicing (e.g. [: -1]).
    """
    with pytest.raises(ValueError, match="limit must be greater than 0"):
        validate_max_records(-1)

    with pytest.raises(ValueError, match="limit must be greater than 0"):
        validate_max_records(0)

    with pytest.raises(ValueError, match="limit must be greater than 0"):
        validate_max_records("-5")

    with pytest.raises(ValueError, match="must be a positive integer"):
        validate_max_records("invalid_string")

def test_max_records_ceiling_enforced_at_20():
    """
    Reviewer Point 2: Advertised 20-record ceiling cannot be raised
    (e.g. max_records=100 must be clamped to 20).
    """
    assert validate_max_records(100) == 20
    assert validate_max_records(50) == 20
    assert validate_max_records(20) == 20
    assert validate_max_records(5) == 5
    assert validate_max_records(1) == 1

def test_replay_mode_accounting_isolated():
    """
    Reviewer Point 1: Replay accounting must be strictly separated from live traffic.
    Replay records live_requests_dispatched = 0 and cached_bytes_read.
    """
    exit_code = run_bounded_probe(replay_mode=True, max_records=10)
    assert exit_code == 0

    artifact_path = os.path.join(os.path.dirname(__file__), "..", "logs", "azure_bounded_probe_result.json")
    assert os.path.exists(artifact_path)

    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)

    assert artifact["status"] == "SUCCESS"
    assert artifact["bounded_accounting"]["accounting_mode"] == "OFFLINE_REPLAY"
    assert artifact["bounded_accounting"]["live_requests_dispatched"] == 0
    assert artifact["bounded_accounting"]["cached_bytes_read"] > 0
    assert artifact["records_parsed"] == 10
    assert artifact["canary_verified"] is True

def test_outer_process_deadline_timeout():
    """
    Reviewer Point 1: Enforces real outer process deadline with failure artifact.
    """
    exit_code = run_bounded_probe(replay_mode=True, timeout_seconds=0.001)
    assert exit_code == 1

    artifact_path = os.path.join(os.path.dirname(__file__), "..", "logs", "azure_bounded_probe_result.json")
    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)

    assert artifact["status"] == "DEADLINE_EXCEEDED"
    assert "outer process deadline" in artifact["failure_reason"].lower()
    assert artifact["canary_verified"] is False
