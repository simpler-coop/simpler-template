# Wiki-style database editing — prototype (ADR 0001)

A minimal web app showing every **must have** from
[ADR 0001](../../documentation/analysis_and_decision_records/0001_wiki_style_database_editing_adr.md):

| Must have | How the prototype does it |
|---|---|
| Database foundation | SQL database (SQLite file by default; plain SQL schema portable to Postgres/Supabase). Tables `entries`, `proposals`, `users`, `settings`. |
| Individual accountability | Every change is a row in `proposals` with `author_id`, timestamps and reviewer. Per-entry **History** page. |
| Submit new entries | "+ Add entry" form. |
| Edit existing entries | "Edit" link on every row. |
| Admin review gate (toggle) | Admin page → "Turn off/on". ON: changes wait in a queue for Approve/Reject. OFF: changes apply instantly (still logged). |
| Simple access | "Continue with GitHub" / "Continue with Google" (OAuth). No passwords. |
| Easy UX | A table and a plain form. No git, no pull requests. |

Nice to have: admins can **ban a user**, which rejects all their pending edits and blocks new ones.

## Run locally

```bash
cd prototypes/wiki_db_editing
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OAuth credentials, then:
set -a; . ./.env; set +a
python app.py          # http://localhost:5000
```

### OAuth setup
* **GitHub:** Settings → Developer settings → OAuth Apps → New. Callback URL `http://localhost:5000/auth/github/callback`. Put the values in `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`.
* **Google:** Cloud Console → APIs & Services → Credentials → OAuth client ID (Web). Redirect URI `http://localhost:5000/auth/google/callback`. Put the values in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
* Admins: `ADMIN_EMAILS=a@b.org,...` and/or `ADMIN_GITHUB_LOGINS=octocat,...`.

### Try it without OAuth
`DEV_LOGIN=1 python app.py` adds a mock "Dev login" form (with an admin checkbox). Never enable it in production.

## Tests
```bash
pytest -q
```
They cover the whole flow: queue → approve/reject → gate off → ban. They also cover the GitHub OAuth callback (mocked HTTP), the CSRF state check, and the dev login being off by default.

## Deploying
Any Python host works (Render, Fly.io, Railway, a VM). For production, point the schema at managed Postgres (e.g. Supabase) and run behind HTTPS.

## License
Contributed under the repository's license (GNU GPL v3, see [LICENSE](../../LICENSE)).
