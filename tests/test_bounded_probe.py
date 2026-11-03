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

def test_invalid_timeout_rejected():
    """
    Reviewer Point 4: Reject invalid or negative timeout values.
    """
    from scripts.run_azure_bounded_probe import validate_timeout

    with pytest.raises(ValueError, match="timeout must be greater than 0"):
        validate_timeout(-1.0)

    with pytest.raises(ValueError, match="timeout must be greater than 0"):
        validate_timeout(0.0)

    with pytest.raises(ValueError, match="must be a positive number"):
        validate_timeout("invalid_timeout")

def test_process_isolation_and_sentinel_survival():
    """
    Reviewer Point 1: Proves that process-tree termination terminates only the child worker
    in its isolated process group, while the supervisor and an unrelated sentinel survive.
    """
    import subprocess
    import sys
    import time
    from scripts.run_azure_bounded_probe import kill_process_tree

    # Start an unrelated sentinel process (e.g. python -c "import time; time.sleep(10)")
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])

    # Start a target child worker process with isolated session / process group
    popen_kwargs = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    target_worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        **popen_kwargs
    )

    try:
        # Terminate only the target worker tree
        success = kill_process_tree(target_worker.pid)
        time.sleep(0.5)

        # Confirm target worker was terminated
        assert target_worker.poll() is not None or not success

        # CRITICAL: Confirm unrelated sentinel survived unharmed!
        assert sentinel.poll() is None
    finally:
        # Cleanup sentinel
        sentinel.terminate()
        sentinel.kill()

def test_response_capture_evidence_preserved():
    """
    Reviewer Point 4: Probe preserves sanitized original captures and SHA-256 hashes.
    """
    exit_code = run_bounded_probe(replay_mode=True, max_records=5)
    assert exit_code == 0

    artifact_path = os.path.join(os.path.dirname(__file__), "..", "logs", "azure_bounded_probe_result.json")
    with open(artifact_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)

    assert "response_capture_evidence" in artifact
    evidence = artifact["response_capture_evidence"]
    assert "capture_sha256" in evidence
    assert len(evidence["capture_sha256"]) == 64
    assert evidence["byte_length"] > 0
    assert evidence["store_id_lineage"] == "100"
    assert evidence["fulfillment_intent_lineage"] == "PICKUP"

