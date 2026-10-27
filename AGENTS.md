# Working on me&my music

Build a useful music manager: browse files, find music through Soulseek, match
albums with MusicBrainz/beets, and import them into a library used by players
such as Navidrome. Finish the requested behavior across the API and UI.

## Project map

- `app/main.py`: FastAPI routes, sessions, validation, file operations and jobs.
- `app/files.py`: root-relative paths, directory listing, metadata and filesystem operations.
- `app/accounts.py`: managed Soulseek login, configuration reload, status and reconnect.
- `app/integrations.py`: slskd HTTP adapter and MusicBrainz requests.
- `app/proxy.py`: shared outbound SOCKS5 configuration, preflight and beets transport.
- `app/tagger.py`: beets matching, reviewed import plans and catalog updates.
- `app/vault.py`: encrypted saved Soulseek credentials.
- `app/store.py`: SQLite jobs and operation history.
- `app/static/`: vanilla JavaScript, HTML and CSS. No frontend build step.
- `app/cli.py`: JSON CLI using the same authenticated HTTP API as the browser.
- `compose.yaml`: standard stack. `compose.proxmox.yaml`: optional host compatibility override.
- `tests/`: backend tests, UI tests and opt-in real slskd integration checks.

## Interface and language

- All user-facing UI, API errors, CLI output and documentation must be English.
- Write short, concrete labels: Library, Inbox, Move, Delete, Match album.
- Avoid personal references, deployment-specific assumptions, marketing banners,
  privacy/security slogans and speculation about geographic restrictions.
- Explain a technical detail only when it helps the user complete an action.
- Keep file operations discoverable in both Library and Inbox. Show details when
  an item is selected rather than filling the page with permanent instruction panels.
- Theme changes must cover every surface, preserve readable contrast, persist
  across reloads and work on the login screen. Use CSS variables.
- Escape file names, tags and remote content before putting them into HTML.

## File operation contracts

- Resolve all paths through `safe_path`. Never accept absolute paths, traversal
  or symlinks as a shortcut around a configured root. Never modify a root itself.
- Never overwrite an existing destination. Keep exclusive filesystem operations
  and the existing operation lock; a preflight existence check alone is insufficient.
- Moves and renames of imported Library files must update beets catalog paths.
  Deletions must remove the corresponding catalog entries.
- Permanent deletion requires an explicit UI confirmation naming the selected
  object and explaining that folder contents are included. A failed request must
  not remove the object from the UI as though it succeeded.
- Do not recursively chmod/chown a music archive or change host ownership to fix
  a container configuration problem.

## Drag-and-drop behavior

- Dragging moves an existing file or folder; it does not upload a browser file.
- Show the destination and reject drops onto the source, its descendants or its
  current parent when the move would do nothing.
- Hovering over a directory or breadcrumb for a short delay opens it while the
  same drag continues. Support several consecutive hover transitions.
- Keep the native drag source attached to the DOM until the drag finishes.
  Replacing its ancestor during hover navigation can cancel the browser drag.
- Leaving a target, cancelling, dropping or encountering an error must clear
  hover timers and highlights. Never move anything merely because it was hovered.
- Keep a graphical folder picker as an alternative to dragging, including for
  touch and keyboard users. Do not require users to type a destination path.
- Test actual pointer-driven dragging in a browser; synthetic event tests alone
  do not establish that the browser preserves a drag across navigation.

## Soulseek and network contracts

- Distinguish slskd API availability, TCP connectivity and confirmed Soulseek login.
  Only `isLoggedIn: true` confirms network login.
- **slskd 0.26.2 leaves `/server.username` null even after a successful login.**
  See `SoulseekAccount.server_status`: once login is confirmed, read the active
  account from `/options`. Do not reintroduce a false timeout by requiring that
  unused field to contain a name.
- Registration uses the Soulseek login protocol with an available username.
- Preserve structured error codes, stages and actionable hints. Do not return raw
  upstream logs, credentials or arbitrary exception strings to the browser.
- Soulseek and proxy credentials belong in the working tmpfs YAML; remembered
  Soulseek credentials are encrypted in state. Clear RAM config and its backup
  on logout. Never commit `.env` or print tokens/passwords.
- `OUTBOUND_PROXY` supports SOCKS5 for external Soulseek and MusicBrainz traffic.
  Internal manager-to-slskd requests must bypass outbound/environment proxies.
- Preserve the pinned slskd version unless compatibility is deliberately verified.

## Download and task interface contracts

- Keep search and transfer trees keyed by username and full remote path. Preserve
  original remote filenames for requests, nested disc folders and expansion state.
- Poll downloads while the signed-in app is open, including outside Downloads.
  Notify on successful transitions only; failed/cancelled terminal transfers are
  not successes. Do not announce old completed downloads on initial page load.
- Download cleanup removes terminal slskd records, never Inbox files or active
  transfers. Keep task cleanup separate from operation history and music files.
- Preview rejection and apply must use mutually exclusive database transitions.
  A cancelled worker must not later restore its result or run queued work.
- Keep active tasks and ready previews during cleanup. Retain cleared terminal
  records for clients already polling an ID. Do not force-stop an import while it
  is publishing files. Keep ready-preview expiry visible to the user.
- The app shell has no top header. Theme/session controls belong in Settings;
  Soulseek connection status belongs in the Soulseek page heading.

## Matching and import contracts

- Prefer embedded album/artist/release tags and track-level matching through
  beets. A folder-name text search is a fallback, not a verified album match.
- Use track names, durations, track counts and missing/extra tracks in matching.
  Display match distance as a ranking, not a guarantee of the correct edition.
- Do not claim audio fingerprint recognition unless that capability is implemented.
- Serialize MusicBrainz access through `musicbrainz_lock` and retain rate limiting
  and the shared proxy transport. Do not globally monkey-patch sockets.
- Preview must not modify music. Import applies a reviewed, unexpired plan,
  verifies source fingerprints and publishes files without overwriting destinations.
- Keep Inbox originals by default. Optional cleanup must be off until selected in
  Settings and shown in the reviewed preview, naming the folder and all contents.
  Delete only after files and catalog commit succeed. Changed folders stay; a
  cleanup failure must not claim that the Library import failed.
- Use `catalog_transaction` savepoints for persistent beets mutations: beets
  2.3.1 commits its normal transaction context even if the body raises.
- Commit Inbox import provenance with the beets catalog. Keep match caches
  separate from transient task lists, invalidate them when track snapshots change,
  and distinguish weak suggestions from an identified album.
- Manual Add to Library copies bytes and nested folders as-is, without a
  MusicBrainz call or tag rewrite. Do not label manual copies as matched.
  Publish the new album folder exclusively and roll back catalog rows on failure.
- Match all runs sequential jobs, groups disc folders and never auto-imports.
  Cancellation and cache publication must be one database transaction.
- System drops upload new files; internal drags move existing archive items.
  Preserve full folder paths and read all directory-entry batches, including past
  Chromium's first 100 entries. Do not require HTTPS-only file-system APIs for
  basic folder drops on a local HTTP deployment.
- Uploads stream to journalled staging and publish exclusively. Reject unsafe
  paths, symlinks, duplicates and destination conflicts; clean cancelled/crashed
  sessions without deleting unrelated archive content.
- Catalog changes and partial failures must not silently leave files missing or
  claim an unfinished import succeeded.

## Development and verification

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.txt
npm ci
.venv/bin/python -m pytest -q
node --check app/static/app.js
npm test
npx playwright install chromium --only-shell
npm run test:browser
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example -f compose.yaml -f compose.proxmox.yaml config --quiet
```

Use a disposable directory or stack for filesystem tests. Cover the behavior
changed: an error path, correct root, destination conflict, catalog consistency,
or a browser interaction. Do not run destructive checks against a real archive.

`scripts/setup.py` creates a new local `.env` and sample directories. It refuses
to overwrite an existing configuration. Start with `docker compose up -d --build`.
The app listens on port 8080; slskd's local API/UI uses 5030.

The CI workflow starts the real pinned Compose stack and runs
`MMM_TEST_SLSKD=1 python -m pytest -q tests/test_slskd_container.py`.
That test checks API/configuration compatibility and does not log into Soulseek.

The manual workflow input `live_soulseek` runs
`python scripts/check_soulseek_login.py`, which creates a disposable public
Soulseek account and checks registration/logout/login. Run it only when explicitly
authorized to contact the public network and create an account. Never enable it
on every push. Keep credentials out of logs and always tear down the test stack.

## Completing a change

Update relevant docs and tests alongside behavior. Keep dependencies justified,
preserve existing environment variable compatibility and inspect the final diff.
Report what changed, what actually passed and any remaining limitations. If an
environment cannot run Docker or access a service, say so; do not describe mocks
or configuration-only checks as successful end-to-end network tests.
