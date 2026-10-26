"""Database management for Wiki-Style Database Editing Prototype.

Implements:
1. Production Table (`records`): Authoritative single source of truth.
2. Staging / Proposed Edits Table (`proposed_edits`): Tracks proposed changes when admin gate is ON.
3. Audit Log Table (`audit_log`): Immutable history of all approved/applied changes.
4. Settings Table (`settings`): Configuration including admin review gate toggle.
5. Users Table (`users`): Registered users with roles (contributor, admin).
"""

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def get_current_iso_time() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: str = "wiki_data.db"):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Users Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'contributor',
                    created_at TEXT NOT NULL
                )
                """
            )

            # 2. Production Records Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_modified_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # 3. Proposed Edits (Staging Review Queue)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS proposed_edits (
                    edit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL, -- 'INSERT', 'UPDATE', 'DELETE'
                    record_id INTEGER,       -- NULL for INSERT proposals
                    proposed_title TEXT,
                    proposed_category TEXT,
                    proposed_content TEXT,
                    previous_title TEXT,
                    previous_category TEXT,
                    previous_content TEXT,
                    submitted_by TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    rejection_reason TEXT,
                    FOREIGN KEY (submitted_by) REFERENCES users (user_id),
                    FOREIGN KEY (reviewed_by) REFERENCES users (user_id)
                )
                """
            )

            # 4. Audit Log Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    record_id INTEGER NOT NULL,
                    field_name TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    applied_by TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    proposal_id INTEGER
                )
                """
            )

            # 5. Settings Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # Seed default settings
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_review_gate', 'true')"
            )

            # Seed default admin and sample contributor
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (user_id, email, name, provider, role, created_at)
                VALUES ('usr_admin_1', 'admin@simpler.coop', 'Lead Admin', 'github', 'admin', ?)
                """,
                (get_current_iso_time(),),
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (user_id, email, name, provider, role, created_at)
                VALUES ('usr_user_1', 'contributor@simpler.coop', 'Community Member', 'google', 'contributor', ?)
                """,
                (get_current_iso_time(),),
            )

            # Seed sample database items if table empty
            cursor.execute("SELECT COUNT(*) as count FROM records")
            count = cursor.fetchone()["count"]
            if count == 0:
                initial_items = [
                    (
                        "Mission Statement",
                        "Governance",
                        "Simpler Co-op organizes shared open-source knowledge and cooperative resources.",
                        "usr_admin_1",
                        get_current_iso_time(),
                        get_current_iso_time(),
                    ),
                    (
                        "Resource Index",
                        "Documentation",
                        "Comprehensive registry of cooperative toolkits, templates, and decentralized apps.",
                        "usr_admin_1",
                        get_current_iso_time(),
                        get_current_iso_time(),
                    ),
                ]
                cursor.executemany(
                    """
                    INSERT INTO records (title, category, content, last_modified_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    initial_items,
                )

            conn.commit()

    # --- Settings Management ---
    def is_review_gate_enabled(self) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'admin_review_gate'")
            row = cursor.fetchone()
            return row["value"].lower() == "true" if row else True

    def set_review_gate(self, enabled: bool) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE settings SET value = ? WHERE key = 'admin_review_gate'",
                ("true" if enabled else "false",),
            )
            conn.commit()

    # --- User Management ---
    def get_or_create_user(
        self, user_id: str, email: str, name: str, provider: str, role: str = "contributor"
    ) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                now = get_current_iso_time()
                cursor.execute(
                    """
                    INSERT INTO users (user_id, email, name, provider, role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, email, name, provider, role, now),
                )
                conn.commit()
                cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                user = cursor.fetchone()
            return dict(user)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # --- Records Management ---
    def list_records(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT r.*, u.name as last_modified_by_name
                FROM records r
                LEFT JOIN users u ON r.last_modified_by = u.user_id
                WHERE r.status = 'active'
                ORDER BY r.record_id ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_record(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM records WHERE record_id = ?", (record_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # --- Submission / Edit Logic ---
    def propose_insert(
        self, title: str, category: str, content: str, user_id: str
    ) -> Dict[str, Any]:
        """Submit a new record. Directly inserts if gate is off; stages if gate is on."""
        gate_enabled = self.is_review_gate_enabled()
        now = get_current_iso_time()

        with self.get_connection() as conn:
            cursor = conn.cursor()

            if not gate_enabled:
                cursor.execute(
                    """
                    INSERT INTO records (title, category, content, last_modified_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (title, category, content, user_id, now, now),
                )
                rec_id = cursor.lastrowid
                # Log audit
                cursor.execute(
                    """
                    INSERT INTO audit_log (operation, record_id, field_name, old_value, new_value, applied_by, applied_at)
                    VALUES ('INSERT', ?, 'record', NULL, ?, ?, ?)
                    """,
                    (rec_id, f"title={title}; category={category}", user_id, now),
                )
                conn.commit()
                return {
                    "action": "inserted",
                    "record_id": rec_id,
                    "status": "applied",
                    "gate_enabled": False,
                }
            else:
                cursor.execute(
                    """
                    INSERT INTO proposed_edits (
                        operation, record_id, proposed_title, proposed_category, proposed_content,
                        submitted_by, submitted_at, status
                    )
                    VALUES ('INSERT', NULL, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (title, category, content, user_id, now),
                )
                edit_id = cursor.lastrowid
                conn.commit()
                return {
                    "action": "staged",
                    "edit_id": edit_id,
                    "status": "pending",
                    "gate_enabled": True,
                }

    def propose_update(
        self, record_id: int, title: str, category: str, content: str, user_id: str
    ) -> Dict[str, Any]:
        """Propose or apply an edit to an existing record."""
        current = self.get_record(record_id)
        if not current:
            raise ValueError(f"Record {record_id} not found")

        gate_enabled = self.is_review_gate_enabled()
        now = get_current_iso_time()

        with self.get_connection() as conn:
            cursor = conn.cursor()

            if not gate_enabled:
                # Apply immediately
                cursor.execute(
                    """
                    UPDATE records
                    SET title = ?, category = ?, content = ?, last_modified_by = ?, updated_at = ?
                    WHERE record_id = ?
                    """,
                    (title, category, content, user_id, now, record_id),
                )
                # Audit log for each changed field
                for field, old_val, new_val in [
                    ("title", current["title"], title),
                    ("category", current["category"], category),
                    ("content", current["content"], content),
                ]:
                    if old_val != new_val:
                        cursor.execute(
                            """
                            INSERT INTO audit_log (operation, record_id, field_name, old_value, new_value, applied_by, applied_at)
                            VALUES ('UPDATE', ?, ?, ?, ?, ?, ?)
                            """,
                            (record_id, field, old_val, new_val, user_id, now),
                        )
                conn.commit()
                return {
                    "action": "updated",
                    "record_id": record_id,
                    "status": "applied",
                    "gate_enabled": False,
                }
            else:
                cursor.execute(
                    """
                    INSERT INTO proposed_edits (
                        operation, record_id, proposed_title, proposed_category, proposed_content,
                        previous_title, previous_category, previous_content,
                        submitted_by, submitted_at, status
                    )
                    VALUES ('UPDATE', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        record_id,
                        title,
                        category,
                        content,
                        current["title"],
                        current["category"],
                        current["content"],
                        user_id,
                        now,
                    ),
                )
                edit_id = cursor.lastrowid
                conn.commit()
                return {
                    "action": "staged",
                    "edit_id": edit_id,
                    "status": "pending",
                    "gate_enabled": True,
                }

    # --- Admin Review Queue Actions ---
    def list_pending_edits(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.*, u.name as submitter_name, u.email as submitter_email
                FROM proposed_edits p
                JOIN users u ON p.submitted_by = u.user_id
                WHERE p.status = 'pending'
                ORDER BY p.submitted_at ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def approve_edit(self, edit_id: int, admin_user_id: str) -> Dict[str, Any]:
        now = get_current_iso_time()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM proposed_edits WHERE edit_id = ?", (edit_id,))
            edit = cursor.fetchone()
            if not edit:
                raise ValueError("Edit proposal not found")
            if edit["status"] != "pending":
                raise ValueError(f"Proposal is already {edit['status']}")

            op = edit["operation"]
            if op == "INSERT":
                cursor.execute(
                    """
                    INSERT INTO records (title, category, content, last_modified_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edit["proposed_title"],
                        edit["proposed_category"],
                        edit["proposed_content"],
                        edit["submitted_by"],
                        now,
                        now,
                    ),
                )
                target_rec_id = cursor.lastrowid
                cursor.execute(
                    """
                    INSERT INTO audit_log (operation, record_id, field_name, old_value, new_value, applied_by, applied_at, proposal_id)
                    VALUES ('INSERT', ?, 'record', NULL, ?, ?, ?, ?)
                    """,
                    (
                        target_rec_id,
                        f"title={edit['proposed_title']}",
                        edit["submitted_by"],
                        now,
                        edit_id,
                    ),
                )
            elif op == "UPDATE":
                target_rec_id = edit["record_id"]
                cursor.execute(
                    """
                    UPDATE records
                    SET title = ?, category = ?, content = ?, last_modified_by = ?, updated_at = ?
                    WHERE record_id = ?
                    """,
                    (
                        edit["proposed_title"],
                        edit["proposed_category"],
                        edit["proposed_content"],
                        edit["submitted_by"],
                        now,
                        target_rec_id,
                    ),
                )
                for field, old_v, new_v in [
                    ("title", edit["previous_title"], edit["proposed_title"]),
                    ("category", edit["previous_category"], edit["proposed_category"]),
                    ("content", edit["previous_content"], edit["proposed_content"]),
                ]:
                    if old_v != new_v:
                        cursor.execute(
                            """
                            INSERT INTO audit_log (operation, record_id, field_name, old_value, new_value, applied_by, applied_at, proposal_id)
                            VALUES ('UPDATE', ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (target_rec_id, field, old_v, new_v, edit["submitted_by"], now, edit_id),
                        )

            # Mark proposal as approved
            cursor.execute(
                """
                UPDATE proposed_edits
                SET status = 'approved', reviewed_by = ?, reviewed_at = ?
                WHERE edit_id = ?
                """,
                (admin_user_id, now, edit_id),
            )
            conn.commit()
            return {"status": "approved", "edit_id": edit_id, "operation": op}

    def reject_edit(
        self, edit_id: int, admin_user_id: str, reason: str = "Rejected by admin"
    ) -> Dict[str, Any]:
        now = get_current_iso_time()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE proposed_edits
                SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, rejection_reason = ?
                WHERE edit_id = ? AND status = 'pending'
                """,
                (admin_user_id, now, reason, edit_id),
            )
            conn.commit()
            return {"status": "rejected", "edit_id": edit_id, "reason": reason}

    # --- Moderation / Rollback User Changes ---
    def revert_all_user_edits(self, target_user_id: str, admin_user_id: str) -> Dict[str, Any]:
        """Rolls back all changes made by a specific user and rejects pending edits."""
        now = get_current_iso_time()
        reverted_count = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Reject all pending proposals by this user
            cursor.execute(
                """
                UPDATE proposed_edits
                SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, rejection_reason = 'Bulk user purge'
                WHERE submitted_by = ? AND status = 'pending'
                """,
                (admin_user_id, now, target_user_id),
            )

            # 2. Find all audit log entries applied by this user in reverse chronological order
            cursor.execute(
                """
                SELECT * FROM audit_log
                WHERE applied_by = ?
                ORDER BY log_id DESC
                """,
                (target_user_id,),
            )
            logs = cursor.fetchall()

            for log in logs:
                rec_id = log["record_id"]
                op = log["operation"]
                field = log["field_name"]
                old_val = log["old_value"]

                if op == "INSERT":
                    # Mark record inactive / deleted
                    cursor.execute(
                        "UPDATE records SET status = 'archived' WHERE record_id = ?",
                        (rec_id,),
                    )
                    reverted_count += 1
                elif op == "UPDATE" and old_val is not None:
                    # Restore previous field value
                    cursor.execute(
                        f"UPDATE records SET {field} = ?, updated_at = ? WHERE record_id = ?",
                        (old_val, now, rec_id),
                    )
                    reverted_count += 1

            conn.commit()
            return {
                "user_id": target_user_id,
                "reverted_actions": reverted_count,
                "status": "purged",
            }

    # --- Audit Log Query ---
    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.*, u.name as user_name
                FROM audit_log a
                LEFT JOIN users u ON a.applied_by = u.user_id
                ORDER BY a.log_id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
