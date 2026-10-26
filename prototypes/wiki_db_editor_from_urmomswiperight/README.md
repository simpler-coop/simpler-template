# Wiki-Style Database Editing Prototype

Working prototype demonstrating collaborative wiki-style database editing with an **Admin Review Gate**, **User Accountability**, and **Audit Tracking**, developed to fulfill [ADR 0001: wiki-style database editing](../../documentation/analysis_and_decision_records/0001_wiki_style_database_editing_adr.md).

## Features & ADR Criteria Fulfillment

| Requirement | ADR Priority | How It's Addressed in This Prototype |
| :--- | :--- | :--- |
| **Database Foundation** | Must Have | Relational database backend (`sqlite3` / PostgreSQL compatible) storing production tables, change-sets, and audit history. |
| **Individual Accountability** | Must Have | Every edit attributes to a verified user (`user_id`, `name`, `email`, OAuth provider) with ISO timestamps. |
| **Submit New Entries** | Must Have | Form / API endpoint allowing users to insert new rows. Staged or applied directly based on gate setting. |
| **Edit Existing Entries** | Must Have | Cell-by-cell and modal editing allowing field-level modifications on existing records. |
| **Admin Review Gate** | Must Have | Configurable toggle (`ON`/`OFF`). When ON, edits enter `proposed_edits` staging queue. Admins inspect side-by-side diffs to Approve or Reject. When OFF, edits apply immediately. |
| **Simple Access (OAuth)** | Must Have | Reusable OAuth profile structure (Google, GitHub) without requiring git knowledge. |
| **Easy UX** | Must Have | Modern web UI styled like a spreadsheet/table with one-click edits and modal inputs. |
| **User Purge / Moderation** | Nice-to-Have | 1-click admin action to roll back all edits made by a spam or compromised account and purge their pending submissions. |
| **Audit Trail** | Core Feature | Immutable chronological log recording `operation`, `record_id`, `field_name`, `old_value`, `new_value`, `applied_by`, and `applied_at`. |

---

## Architecture Overview

```
                      ┌────────────────────────────┐
                      │    Contributor / User      │
                      │   (Google / GitHub Auth)   │
                      └─────────────┬──────────────┘
                                    │ Submit New / Edit
                                    ▼
                      ┌────────────────────────────┐
                      │   Admin Review Gate Check  │
                      └───────┬────────────┬───────┘
                     Gate ON  │            │ Gate OFF
                              ▼            │
         ┌─────────────────────────┐       │
         │ Staging Queue Table     │       │
         │ (`proposed_edits`)      │       │
         └────────────┬────────────┘       │
                      │ Admin Review       │
              Approve │ (or Reject)        │
                      ▼                    │
         ┌─────────────────────────┐       │
         │ Primary Production Table│◄──────┘
         │ (`records`)             │
         └────────────┬────────────┘
                      │
                      ▼
         ┌─────────────────────────┐
         │ Immutable Audit Log     │
         │ (`audit_log`)           │
         └─────────────────────────┘
```

---

## Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Web Dashboard
```bash
python app.py
```
Open your browser to:
```
http://localhost:8000
```

### 3. Run Automated Tests
```bash
pytest tests/test_wiki_editor.py -v
```
All 7 verification tests validate every Must-Have and Nice-to-Have criterion automatically.

---

## User Roles & Persona Switching
In the prototype header, use the **Persona Switcher** dropdown to test different roles:
- **Bob Contributor (Google):** Can submit new entries and edit existing records.
- **Alice Admin (GitHub):** Can toggle the Admin Review Gate, view and approve/reject pending submissions, and purge spam users.
- **Spam Bot (GitHub):** Demonstrates malicious/unreliable edits that an admin can revert in one click.
