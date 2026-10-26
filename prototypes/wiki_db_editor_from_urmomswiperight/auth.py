"""Authentication and user session management for the Wiki Database Editor.

Supports:
1. Re-usable OAuth identity simulation (Google and GitHub profiles).
2. Cookie/Session authentication.
3. Role-based authorization (`contributor` vs `admin`).
"""

from typing import Dict, Optional
from database import Database

# Pre-configured demo profiles for testing and evaluation
DEFAULT_PROFILES = {
    "admin": {
        "user_id": "usr_admin_1",
        "email": "admin@simpler.coop",
        "name": "Alice Admin",
        "provider": "github",
        "role": "admin",
    },
    "contributor": {
        "user_id": "usr_user_1",
        "email": "contributor@simpler.coop",
        "name": "Bob Contributor",
        "provider": "google",
        "role": "contributor",
    },
    "spammer": {
        "user_id": "usr_spammer_9",
        "email": "spammer@unreliable.org",
        "name": "Spam Bot",
        "provider": "github",
        "role": "contributor",
    },
}


class AuthManager:
    def __init__(self, db: Database):
        self.db = db
        self._ensure_default_users()

    def _ensure_default_users(self):
        for profile in DEFAULT_PROFILES.values():
            self.db.get_or_create_user(
                user_id=profile["user_id"],
                email=profile["email"],
                name=profile["name"],
                provider=profile["provider"],
                role=profile["role"],
            )

    def resolve_user(self, session_user_id: Optional[str]) -> Dict:
        """Resolve current user or fallback to standard contributor."""
        user_id = session_user_id or "usr_user_1"
        user = self.db.get_user(user_id)
        if not user:
            user = self.db.get_user("usr_user_1")
        return user or DEFAULT_PROFILES["contributor"]
