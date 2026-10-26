"""Automated test suite verifying all ADR-0001 Must-Have and Nice-to-Have criteria."""

import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from database import Database
from auth import AuthManager
import app as app_module


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    import gc
    gc.collect()
    try:
        if os.path.exists(path):
            os.remove(path)
    except (PermissionError, OSError):
        pass


@pytest.fixture
def client(temp_db):
    app_module.db = temp_db
    app_module.auth_manager = AuthManager(temp_db)
    return TestClient(app_module.app)


def test_must_have_1_database_foundation(temp_db):
    """Must Have 1: Data lives in a proper structured database, not plain text."""
    records = temp_db.list_records()
    assert len(records) >= 2
    assert records[0]["title"] == "Mission Statement"
    assert "record_id" in records[0]
    assert "last_modified_by" in records[0]


def test_must_have_2_individual_accountability(temp_db):
    """Must Have 2: Every user has an account and changes track author identity."""
    user = temp_db.get_or_create_user(
        user_id="usr_test_123",
        email="test@user.org",
        name="Test User",
        provider="github",
        role="contributor",
    )
    assert user["user_id"] == "usr_test_123"
    assert user["provider"] == "github"

    # Make change and check attribution
    temp_db.set_review_gate(False)
    res = temp_db.propose_insert(
        title="Accountability Item",
        category="Testing",
        content="Testing user attribution",
        user_id="usr_test_123",
    )
    rec = temp_db.get_record(res["record_id"])
    assert rec["last_modified_by"] == "usr_test_123"


def test_must_have_3_and_5_submit_new_with_gate_on(temp_db):
    """Must Have 3 & 5: Submitting new entry with Admin Review Gate ON stages for review."""
    temp_db.set_review_gate(True)
    assert temp_db.is_review_gate_enabled() is True

    result = temp_db.propose_insert(
        title="Pending Proposal",
        category="Proposals",
        content="Waiting for admin approval",
        user_id="usr_user_1",
    )
    assert result["status"] == "pending"
    assert result["gate_enabled"] is True

    pending = temp_db.list_pending_edits()
    assert len(pending) == 1
    assert pending[0]["proposed_title"] == "Pending Proposal"
    assert pending[0]["submitted_by"] == "usr_user_1"

    # Approve proposal
    approval = temp_db.approve_edit(pending[0]["edit_id"], admin_user_id="usr_admin_1")
    assert approval["status"] == "approved"

    # Verify committed to production database
    records = temp_db.list_records()
    titles = [r["title"] for r in records]
    assert "Pending Proposal" in titles


def test_must_have_4_edit_existing_entries(temp_db):
    """Must Have 4: Individual users can edit data in existing fields."""
    temp_db.set_review_gate(False)
    rec = temp_db.list_records()[0]
    rec_id = rec["record_id"]

    res = temp_db.propose_update(
        record_id=rec_id,
        title="Updated Mission",
        category="Updated Governance",
        content="Updated content value",
        user_id="usr_user_1",
    )
    assert res["status"] == "applied"

    updated_rec = temp_db.get_record(rec_id)
    assert updated_rec["title"] == "Updated Mission"
    assert updated_rec["category"] == "Updated Governance"

    # Check audit trail
    audit = temp_db.get_audit_trail()
    assert len(audit) >= 1
    assert any(a["record_id"] == rec_id and a["field_name"] == "title" for a in audit)


def test_must_have_5_admin_gate_rejection_workflow(temp_db):
    """Must Have 5: Admins can reject invalid or spam proposals."""
    temp_db.set_review_gate(True)
    res = temp_db.propose_insert(
        title="Bad Entry",
        category="Spam",
        content="Unwanted text",
        user_id="usr_spammer_9",
    )
    edit_id = res["edit_id"]

    # Reject
    rej = temp_db.reject_edit(edit_id, admin_user_id="usr_admin_1", reason="Violates guidelines")
    assert rej["status"] == "rejected"

    # Verify not in records
    records = temp_db.list_records()
    assert not any(r["title"] == "Bad Entry" for r in records)


def test_nice_to_have_2_admin_purge_user_edits(temp_db):
    """Nice to Have 2: Admins can completely roll back all edits made by a specific user."""
    temp_db.set_review_gate(False)  # Apply directly
    target_rec = temp_db.list_records()[0]
    rec_id = target_rec["record_id"]
    original_title = target_rec["title"]

    # Spammer defaces record
    temp_db.propose_update(
        record_id=rec_id,
        title="DEFACED BY SPAMMER",
        category=target_rec["category"],
        content="Malicious payload",
        user_id="usr_spammer_9",
    )
    defaced = temp_db.get_record(rec_id)
    assert defaced["title"] == "DEFACED BY SPAMMER"

    # Spammer adds a bogus record
    res = temp_db.propose_insert(
        title="Bogus Record",
        category="Spam",
        content="junk",
        user_id="usr_spammer_9",
    )
    bogus_id = res["record_id"]

    # Admin triggers purge/revert
    purge_res = temp_db.revert_all_user_edits(
        target_user_id="usr_spammer_9", admin_user_id="usr_admin_1"
    )
    assert purge_res["status"] == "purged"
    assert purge_res["reverted_actions"] >= 2

    # Check that original record is restored
    restored = temp_db.get_record(rec_id)
    assert restored["title"] == original_title

    # Check that bogus record was archived
    bogus_rec = temp_db.get_record(bogus_id)
    assert bogus_rec["status"] == "archived"


def test_api_endpoints(client):
    """Test REST API interaction."""
    # List records
    res = client.get("/api/records")
    assert res.status_code == 200
    assert "records" in res.json()

    # Get gate status
    res = client.get("/api/gate")
    assert res.status_code == 200
    assert "admin_review_gate" in res.json()

    # Get active user
    res = client.get("/api/me")
    assert res.status_code == 200
    assert "user" in res.json()
