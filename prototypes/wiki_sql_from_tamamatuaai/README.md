# SQLite contribution-review prototype

A locally runnable proposal for issue #4. Python 3.9+ and its standard library are the only runtime requirements. No package installation, cloud service, or database account is needed for the demonstration.

## Start the demonstration

From the repository root:

```sh
sh prototypes/wiki_sql/run.sh --demo
```

Open **http://127.0.0.1:8000**. The application binds only to loopback. Stop with Ctrl-C. Use `--port 8001` if the default port is occupied.

1. Choose **Try as contributor**. Fill a blank Description field, then submit for review. The published field remains blank; the history records a pending contribution and its author.
2. Sign out. Choose **Try as administrator**. Review the before/after values and approve. The published field now contains the contribution.
3. Edit an existing Name field and reject that contribution. The published name stays unchanged.
4. Turn review off and submit another edit. It publishes immediately with author attribution. Turn review on to restore approvals. Toggling does not silently approve old pending contributions.
5. Submit two competing changes to the same original field value. Approving one causes the other to be marked `conflict`, rather than overwriting newer data. The contributor can reload and submit a revised change.

Demo identities are synthetic and deliberately selectable. Demo mode is not authentication. It is loopback-only and stores data in `.data/demo.sqlite3`, separate from real-login data.

## Real GitHub login

The administrator registers a GitHub OAuth app under an existing GitHub account:

- Homepage: `http://127.0.0.1:8000`
- Callback: `http://127.0.0.1:8000/callback`

Set these variables in your local shell (never commit secrets):

```sh
export GITHUB_CLIENT_ID='your-client-id'
export GITHUB_CLIENT_SECRET='your-client-secret'
export ADMIN_GITHUB_IDS='12345678'
sh prototypes/wiki_sql/run.sh
```

`ADMIN_GITHUB_IDS` is a comma-separated list of immutable numeric GitHub account IDs, not usernames. This makes renaming an account irrelevant to administrator permissions. The app fails closed when configuration is missing. Visitors choose **Continue with GitHub**; their numeric ID and public login are persisted. No email or repository scope is requested. OAuth access tokens are used once to retrieve the identity and are not stored. OAuth state is single-use, bound to the browser session; a PKCE verifier is included. Sessions expire after an hour and disappear on restart.

The live OAuth flow requires administrator-supplied client credentials. Automated tests mock GitHub responses; they do not claim a live provider login was exercised.

## Data and review model

SQLite `records` holds canonical field values; `changes` holds proposed before/after values, author ID, status, optional reviewer ID, and creation time. Pending data never writes into canonical record fields. Review and application of a change occur in one database transaction. Existing seeded records include empty fields; adding tables/columns is intentionally not exposed to contributors. Usernames are presentation labels; attribution uses stable user IDs.

Any authenticated contributor may read the shared records and contribution history. Only configured admins can change the gate or review. Editing, review, demo selection, and logout require a session CSRF token. All rendered user content is HTML escaped. Field names are allowlisted; values use SQL parameters. Host checks reject non-loopback Host headers. No GitHub callback values are logged.

## Tests

```sh
cd prototypes/wiki_sql
python3 -m unittest -v
```

Tests use temporary databases and local HTTP connections only. They cover database persistence, blank/existing field changes, approvals/rejections, gate behavior, attribution, conflicts, unauthorized requests, CSRF, escaped content and mocked OAuth state/identity handling.

## Limits and tradeoffs

This is a **local prototype**, not a production hosting recipe. It intentionally uses the Python development HTTP server, in-memory sessions, local HTTP cookies, and SQLite. A shared deployment needs a production web server, HTTPS/Secure cookies, durable sessions, rate limiting, backup/retention policy, monitoring, and explicit membership policy. The prototype permits any GitHub account to contribute; it does not grant administrator rights automatically. Do not expose its port through a public tunnel.

It does not implement live co-editing, moderation of all historical edits by a user, a schema editor, or record creation/deletion. These are not necessary to demonstrate the seven must-haves. Concurrent edits are detected by comparing the field's current value with the submitted before-value; this is optimistic conflict detection, not full collaborative merge/versioning. SQLite and standard-library code keep the demo small, but a maintained web framework would be preferable before expanding authentication or deployment scope.
