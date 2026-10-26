"""FastAPI Application for Wiki-Style Database Editing.

Provides both an interactive web dashboard and a clean REST API meeting all
requirements specified in ADR 0001:
- Database foundation (SQLite/PostgreSQL)
- Individual accountability & OAuth identity
- Direct new record submissions and field edits
- Configurable Admin Review Gate (Staging vs Direct Commit)
- Audit log & bulk user revert for spam moderation
"""

import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import AuthManager, DEFAULT_PROFILES
from database import Database

# Initialize database and auth
DB_FILE = os.getenv("WIKI_DB_FILE", "wiki_data.db")
db = Database(DB_FILE)
auth_manager = AuthManager(db)

app = FastAPI(
    title="Simpler Co-op Wiki Database Editor",
    description="Collaborative wiki-style database editing prototype with admin staging gate and audit tracking.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Request/Response Models ---
class RecordInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)


class GateToggleInput(BaseModel):
    enabled: bool


class SwitchUserInput(BaseModel):
    user_id: str


class RejectionInput(BaseModel):
    reason: Optional[str] = "Changes did not meet standards"


class RevertUserInput(BaseModel):
    user_id: str


# Helper to get current user from cookie or fallback
def get_current_user(request: Request):
    user_id = request.cookies.get("session_user_id") or "usr_user_1"
    return auth_manager.resolve_user(user_id)


# --- Web UI Endpoint ---
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Wiki Database Editor API is running. UI not found.</h1>")


# --- Authentication & User Endpoints ---
@app.get("/api/me")
def get_me(request: Request):
    user = get_current_user(request)
    return {"user": user, "available_profiles": DEFAULT_PROFILES}


@app.post("/api/auth/switch")
def switch_user(input_data: SwitchUserInput, response: Response):
    user = db.get_user(input_data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    response.set_cookie(key="session_user_id", value=user["user_id"], httponly=True)
    return {"message": f"Switched to user {user['name']}", "user": user}


# --- Database Records Endpoints ---
@app.get("/api/records")
def get_all_records():
    return {"records": db.list_records()}


@app.get("/api/records/{record_id}")
def get_record(record_id: int):
    rec = db.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"record": rec}


@app.post("/api/records")
def create_record(input_data: RecordInput, request: Request):
    user = get_current_user(request)
    result = db.propose_insert(
        title=input_data.title,
        category=input_data.category,
        content=input_data.content,
        user_id=user["user_id"],
    )
    return result


@app.put("/api/records/{record_id}")
def update_record(record_id: int, input_data: RecordInput, request: Request):
    user = get_current_user(request)
    try:
        result = db.propose_update(
            record_id=record_id,
            title=input_data.title,
            category=input_data.category,
            content=input_data.content,
            user_id=user["user_id"],
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# --- Admin Review Gate & Queue Endpoints ---
@app.get("/api/gate")
def get_gate_status():
    return {"admin_review_gate": db.is_review_gate_enabled()}


@app.post("/api/gate")
def toggle_gate(input_data: GateToggleInput, request: Request):
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can toggle the review gate")
    db.set_review_gate(input_data.enabled)
    return {"admin_review_gate": input_data.enabled, "updated_by": user["name"]}


@app.get("/api/pending")
def get_pending_edits(request: Request):
    return {"pending_edits": db.list_pending_edits()}


@app.post("/api/pending/{edit_id}/approve")
def approve_pending_edit(edit_id: int, request: Request):
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can approve changes")
    try:
        res = db.approve_edit(edit_id, admin_user_id=user["user_id"])
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pending/{edit_id}/reject")
def reject_pending_edit(edit_id: int, input_data: RejectionInput, request: Request):
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can reject changes")
    try:
        res = db.reject_edit(edit_id, admin_user_id=user["user_id"], reason=input_data.reason or "")
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Moderation & Audit Endpoints ---
@app.post("/api/admin/revert-user")
def revert_user_edits(input_data: RevertUserInput, request: Request):
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can purge user contributions")
    res = db.revert_all_user_edits(target_user_id=input_data.user_id, admin_user_id=user["user_id"])
    return res


@app.get("/api/audit")
def get_audit_log(limit: int = 50):
    return {"audit_trail": db.get_audit_trail(limit=limit)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
