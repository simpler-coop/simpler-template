# Wiki-style database editing prototype

A small web app where many people edit a shared SQLite table, with accounts, an optional admin
review gate, and live updates. Python 3 standard library only; nothing to install.
Design and trade-offs are in
[ADR 0001](../../documentation/analysis_and_decision_records/0001_wiki_style_database_editing_adr.md).

## Run it

```sh
sh prototypes/wiki_db/run.sh
```

Open <http://127.0.0.1:8000>. The data is stored in `prototypes/wiki_db/wiki.db`
(delete the file to start over).

Try it:

1. Log in with the demo login as any name (e.g. `Sam`). Click **Edit** on a row, fill a blank
   field, **Save**. With the review gate on you are told the change waits for review.
2. In a second browser (or private window) log in as `admin`, open **Admin**, and approve
   or reject the change. Use the toggle to turn the review gate off, then repeat step 1:
   edits now appear immediately.
3. Keep both windows open on the table: changes show up live without a refresh.
4. As admin, use **Remove all edits and block** next to a user to roll back everything they did.

## Real logins (GitHub / Google)

Set the credentials of an OAuth app whose callback URL is `<WIKI_BASE_URL>/callback/github`
(or `/callback/google`):

```sh
export GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...
export GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...
export WIKI_BASE_URL=http://127.0.0.1:8000
export WIKI_ADMINS=github:<numeric id>,google:<sub>   # who is an admin
export WIKI_DEMO=0                                    # turn the no-password demo login off
```

Accounts are keyed by the provider's numeric id, not the display name. Never leave the demo
login on for a public deployment.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | port to listen on (localhost only) |
| `WIKI_DB` | `wiki.db` | SQLite file |
| `WIKI_DEMO` | `1` with `run.sh` | enable name-only demo login |
| `WIKI_ADMINS` | `demo:admin` with `run.sh` | comma list of `provider:id` admins |

## Tests

```sh
python3 -m unittest discover -s prototypes/wiki_db -p 'test_*.py' -v
```

## Files

* `store.py`: SQLite schema and all rules (accounts, review gate, conflicts, rollback).
* `app.py`: HTTP pages, OAuth/demo login, live-update stream.
* `test_wiki_db.py`: one named test per must-have and nice-to-have.
