# me&my music

A music library manager combining file browsing, Soulseek downloads and album
matching with MusicBrainz/beets. Use the same Library directory with Navidrome or
another music server.

## Features

- Browse Library and Inbox, inspect tags and play supported audio.
- Search file and folder names throughout Library, including nested directories.
  Results show their parent folders and offer Open containing folder.
- See total Library track duration in the heading: Library (time).
- Drop files or folders from your computer into either section, or use Upload →
  Files / Folder. Nested paths are retained; existing files are never overwritten.
- Add an Inbox album directly to a chosen Library folder without MusicBrainz.
  The graphical picker can create artist folders and change the album folder name.
  Manual copies retain all files, artwork, existing tags and nested folders.
- Inbox shows Matched, Needs review and Imported badges. Match all queues album
  folders under the current location; each result still requires import approval.
- Create folders, rename, move and delete files or whole folders in either section.
- Drag items onto folders or breadcrumbs. Hold over a destination for 650 ms to
  open it without releasing the drag; continue browsing, then drop in the desired
  folder. The Move button provides a graphical folder picker as an alternative.
- Browse a history timeline grouped by day and filter operations; undo moves,
  renames and creation of empty folders.
  Deletion is permanent and requires confirmation.
- Choose Original, Light, Dark, Gruvbox or Catppuccin Mocha. Theme selection is
  available in Settings, remembered in the browser and applied to the login screen too.
  Manager sign-out is also in Settings; Soulseek connection status is in its tab.
- Browse Soulseek search results and downloads as user/folder/file trees. Download
  a file or an entire branch, including nested disc folders.
- Filter Soulseek results by filename, folder, user, format or free upload slots.
  Sort users by speed, queue, name, file count or size; show more users in batches
  of 25. Folder downloads include only files matching the current filters.
- Inspect file duration, format, bitrate, VBR, bit depth and sample rate when
  provided by the peer, alongside file sizes, upload speeds and queues. Format
  filters use filename extensions; M4A is separate because its codec is unknown.
- Search updates arrive without overlapping polls or rebuilding unchanged trees.
  Stop keeps the results already found; an interrupted request can be retried.
- Choose Standard or Extended Soulseek search. Extended waits longer for replies
  and accepts more results; it does not guarantee a complete catalog. Progress
  shows reported files and replies while the file list is being collected.
- Search summaries count audio and other files separately. Use Audio files to
  hide artwork and archives, or Clear filters to restore the complete result list.
- Sort downloads by time, name, status, progress or size; filter active, successful
  or failed transfers. Clear finished records without deleting downloaded files.
- Receive in-app notices when a file or selected folder finishes downloading,
  including while viewing another page. Browser notifications can be enabled in
  Settings where supported (usually HTTPS or localhost); keep the app open.
- Cancel a queued/running preview or reject one awaiting review. Clear finished
  tasks while keeping active work and previews awaiting a decision.
- Register or sign in to Soulseek and verify actual network login.
- Match Inbox albums using embedded metadata, track names, durations and track
  counts through beets. Review ranked editions, missing/extra tracks and proposed
  tags before importing. Correct the artist/album or enter an exact release MBID.
- Import copies into Library, with a beets catalog and a JSON CLI for automation.

This is an early release. Metadata matching is not audio fingerprint recognition.
Artwork and non-audio files are not copied automatically. Inbox cleanup is off
by default and can be enabled in Settings. Moves stay within the
selected Library or Inbox root. Import is the workflow from Inbox to Library.

## Quick start

Requires Docker Engine with Docker Compose v2 and Python 3.

```sh
git clone https://github.com/fxmfxmfx/meandmymusic.git
cd meandmymusic
python3 scripts/setup.py
```

Edit `.env`. Set `MUSIC_PATH` to the existing music directory, then choose separate
paths for Inbox and application state. None of these directories may be nested
inside another. For example:

```dotenv
MUSIC_PATH=/srv/music
INBOX_PATH=/srv/music-inbox
STATE_PATH=/srv/meandmymusic/state
SLSKD_STATE_PATH=/srv/meandmymusic/slskd
PUID=1000
PGID=1000
APP_BIND=127.0.0.1
APP_PORT=8080
NAVIDROME_URL=https://music.example.com
```

Use the UID/GID that can read and write these host directories. The setup script
creates a new `.env` with a generated `APP_PASSWORD` and raw `SLSKD_API_KEY`; it
refuses to overwrite an existing configuration. It does not change ownership of
an existing music archive. Preserve the generated passwords when editing paths.

```sh
docker compose up -d --build
```

Open `http://localhost:8080` and sign in using `APP_PASSWORD` from `.env`. Change
`APP_BIND` to a reachable host address for LAN access. Use HTTPS or VPN for remote
access. Keep `.env` private; never publish it or include it in bug reports.

## Library time

The **Library (time)** heading shows the summed duration of readable
tracks, including existing archives, uploads and manual imports. The first count
runs in the background; unchanged files are cached in application state. Returning
to Library rechecks the inventory after one minute. The Library Refresh button
starts an immediate scan after external changes. Inbox, hidden staging, symbolic
links and unreadable durations are excluded. Audio and tags are kept unchanged.

Search Library matches names across the entire Library even when opened inside
an album folder. Clear the query to return to the browsed folder; Load more
results retrieves the next page. Rename, move, delete and playback use each
result's full path.

## Soulseek

Open **Settings → Soulseek**, sign in with a Soulseek network account, or choose
**Create account** and an available username. A forum account is different.
Registration and login use the same Soulseek protocol. The manager waits for
slskd to report `isLoggedIn: true`; reaching the API or TCP port is insufficient.
The slskd 0.26.2 `/server.username` field is unused, so the manager obtains the
active name from `/options` after network login is confirmed.

**Remember account** stores the account encrypted in the state directory and
reconnects after a slskd restart. The encryption key depends on `APP_PASSWORD`;
changing it requires signing in again. Soulseek credentials are entered in the UI,
not in `.env`. The working slskd YAML and its backup live on tmpfs and are cleared
on logout. The slskd administrative web account is separate from Soulseek:
Compose uses username `archive` and `APP_PASSWORD`.

Sharing is opt-in. To share the mounted Library, add `SLSKD_SHARED_DIR: /music`
to the slskd environment in Compose. Soulseek peer connectivity may require
forwarding `SOULSEEK_PORT` (default 50300). Do not expose slskd's administrative
port 5030 publicly.

### Shared SOCKS5 proxy

A proxy is optional. To use an existing SOCKS5 proxy for Soulseek and MusicBrainz:

```dotenv
OUTBOUND_PROXY=socks5://proxy.example.com:1080
SOULSEEK_LOGIN_TIMEOUT=45
```

With authentication:

```dotenv
OUTBOUND_PROXY='socks5://USER:PASSWORD@proxy.example.com:1080'
```

Percent-encode special characters in credentials, such as `@` as `%40`, `:` as
`%3A` and `$` as `%24`. HTTP/HTTPS proxies cannot carry the shared Soulseek route.
Leave `OUTBOUND_PROXY=` empty for direct connections. Internal requests to slskd
always bypass this proxy.

For a proxy on the Docker host, use `host.docker.internal`. The proxy must listen
on an interface reachable from the Docker bridge; a host-only `127.0.0.1` listener
is not sufficient. A proxy on another machine requires that machine's address.
The setting connects to a proxy; it does not install or start one.

After changing `.env`, recreate the stack and sign in or reconnect:

```sh
docker compose up -d --build --force-recreate
```

The manager checks SOCKS5 negotiation, optional proxy authentication and CONNECT
to Soulseek before attempting account login. Successful preflight does not mean
successful Soulseek login.

### Existing slskd instance

Point the manager's `SLSKD_URL` to the existing service and supply its raw
`SLSKD_API_KEY` with the Administrator role. Disable `SLSKD_MANAGED_AUTH` to keep
credentials managed by the external instance. To manage login through this UI,
use `/run/slskd/slskd.yml` on tmpfs, enable remote configuration, and configure
`SLSKD_MANAGED_AUTH=true`, `SLSKD_ADMIN_USER` and `SLSKD_ADMIN_PASSWORD` on the
manager. Mount the same physical download directory as `/inbox`. Remove the
bundled slskd service and its `depends_on` if they are unnecessary.

## Match and import an album

1. Download a complete album from Soulseek. Wait for every track to finish.
2. In Inbox, select the album folder and choose **Match album**.
3. Matching reads embedded tags and compares tracks through beets. Review the
   suggested artist/album. If tags are missing, the folder name is a fallback;
   fill in the artist and album to improve the search.
   Weak matches trigger an additional exact album-title search and searches for
   recordings matching several local track titles. Releases shared by multiple
   track results are considered even when the artist/album credit differs.
   No artist filter is required for these fallback searches. Results are scored
   against the actual local tracks. A unique, complete match scoring at least 95%
   with a ten-point lead opens its preview automatically; it never imports files
   without review. Existing release IDs do not prevent this fallback.
4. Compare edition, date, medium, track count and match score. A percentage ranks
   candidates; it does not guarantee that a particular pressing is correct.
5. Choose **Preview**, inspect each track's old/new tags and destination path,
   then choose **Import album**. Progress remains available in Tasks.

An exact MusicBrainz release MBID or release URL can be entered instead of search.
Incomplete albums, duplicate targets and existing destination files block import.
Previews expire after one hour and fingerprint source files. Changed source files
require a new preview. Original files remain in Inbox by default; optional
cleanup in Settings is shown explicitly before import.

The current import layout is:

```text
Album Artist/Year - Album/Disc-Track Title.ext
```

Navidrome sees imports on its next scan. Automatic scan triggering is not included.

## Tasks and cleanup

**Downloads → Clear finished** removes succeeded, failed and cancelled transfer
records from slskd. Files in Inbox and active transfers stay in place.

**Inbox → Add to Library** copies a selected folder to a destination chosen in
Library, without any MusicBrainz request or metadata rewrite. Create an artist
folder in the picker if needed, choose the album folder name and review the copy
preview. The destination album folder must not exist; it is published without
replacing existing content. Audio files are cataloged with their existing tags;
manual import does not claim a MusicBrainz match. Optional Inbox cleanup is also
shown and confirmed in this preview.

**Inbox → Match all** discovers album folders recursively under the current
location, grouping CD1/CD2 folders as one album. It queues up to 200 albums and
skips imported albums, saved matches and active matching jobs. Matching runs
sequentially in the background. Review each result in Tasks; strong matches have
an import preview ready, while ambiguous results let you choose a release.
No album is imported just by pressing Match all.

Matched/review badges use a saved snapshot of track paths, sizes and modification
times; changing files invalidates the saved match. Import provenance is committed
with the beets catalog and survives task cleanup and manager restarts. Moves
through the manager keep it in sync. Imports made before provenance tracking was
introduced cannot be reliably labelled retroactively. External moves may require
matching again.

**Settings → Delete Inbox folder after import** enables optional cleanup. New
previews name the source folder and explain that all contents, including artwork,
will be permanently deleted after copying and cataloging succeeds. Changing the
setting requires a new preview. A changed source folder is kept; a cleanup failure
reports a warning while retaining the successful Library import. Inbox originals
are kept when the option is off.

**Tasks → Reject** discards a preview awaiting approval. **Cancel** discards queued
or running preview work; a network request already underway may finish, but its
result cannot become importable. An import already copying/tagging files must
finish. **Clear finished** hides completed, applied, failed, cancelled, expired
and interrupted tasks. It keeps ready previews and active tasks; operation history
is retained. Cleared task IDs remain readable by polling clients.

## Uploads

Drop files or folders onto the Library/Inbox page or a destination folder. The
Upload menu also provides file and folder pickers. Directory selection/drop
support depends on the browser; Chromium, Firefox and Safari support common
folder workflows. File selection is available as a fallback.

Uploads preserve relative paths, including empty folders when provided by the
browser's drop API. Limits are 10,000 files and 10,000 folder entries per upload,
50 GiB per file and four concurrent upload sessions. Files stream to temporary
staging on disk, then publish without overwriting existing files. An interrupted
or cancelled upload does not publish partial files; abandoned staging is removed
on expiry or server restart. Keep the page open until the upload finishes.
Uploaded Library files are available directly to Navidrome on its next scan;
Inbox uploads can be matched and imported as usual.

## CLI and API

See [docs/agents.md](docs/agents.md) for commands and a download/import workflow.
Contributors and coding agents should read [AGENTS.md](AGENTS.md).

The CLI uses the same authenticated API as the UI:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export MMM_URL=http://localhost:8080
# Set MMM_PASSWORD using the shell or a secret manager.
bin/mmm status
bin/mmm ls --root inbox
```

With Compose, the CLI can run inside manager using its existing dependencies and
server password, without installing Python packages on the host:

```sh
docker compose exec -T manager python -m app.cli \
  --password-env APP_PASSWORD status
docker compose exec -T manager python -m app.cli \
  --password-env APP_PASSWORD search "Arvo Part Alina" --extended
```

After starting a search, the CLI collects results for up to 60 seconds by default.
Use `--wait 0` to return a search ID immediately or `--wait 120` to collect results
for longer. A search may continue
after the CLI wait ends; use `results SEARCH_UUID` to fetch its current snapshot.

API sessions use `POST /api/login` and the returned cookie. Mutating requests
require `X-Requested-With: meandmymusic`. Errors return `detail`; connection errors
also include `error.code`, `error.stage`, `error.message` and `error.hint`.

## Updates and troubleshooting

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 manager slskd
```

For installations predating managed Soulseek login:

```sh
python3 scripts/upgrade.py
# Add --lan only if the app should bind to all host interfaces.
docker compose up -d --build --force-recreate
```

`upgrade.py` preserves existing application/API passwords, removes obsolete
Soulseek credentials from `.env` and sets configuration file permissions.

| Error | Check |
| --- | --- |
| `INVALID_PASSWORD` | Existing account password or an already taken username |
| `INVALID_USERNAME` | Soulseek username |
| `LOGGED_IN_ELSEWHERE` | Another client using the same account |
| `SLSKD_ADMIN_AUTH_FAILED` | Administrative slskd username/password, separate from Soulseek |
| `SLSKD_HTTP_401` / `SLSKD_HTTP_403` | API key and Administrator role |
| `PROXY_REFUSED` | Proxy listening address and port, reachable from Docker |
| `PROXY_AUTH_FAILED` | SOCKS5 proxy credentials |
| `PROXY_DNS_FAILED` | Proxy hostname and container DNS |
| `PROXY_TARGET_UNREACHABLE` | Proxy is reachable but CONNECT to Soulseek failed |
| `LOGIN_TIMEOUT` | Network/account details and recent slskd logs |

`SOULSEEK_LOGIN_TIMEOUT` accepts 5–120 seconds. Only new log records from the
current attempt are classified; old failures do not invalidate a later attempt.
Raw upstream logs and credentials are not returned to the UI.

If logs show `API key must be between 16 and 255 characters`, use the raw
`SLSKD_API_KEY` in `.env`; Compose adds `role=Administrator;` for slskd.

If startup fails at `socketpair(AF_UNIX)` with `PermissionError`, check host
AppArmor audit logs. On affected Proxmox hosts, the optional override relaxes
AppArmor for these two services while retaining the standard seccomp profile:

```sh
docker compose -f compose.yaml -f compose.proxmox.yaml up -d --build
```

Use the same Compose files for subsequent update/log/stop commands. This is a
host-policy issue before music directories are accessed. For directory errors,
check mount paths and PUID/PGID instead of recursively changing archive ownership.

## Development

Python 3.12+, Node.js and Docker Compose are used in CI:

```sh
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.txt
npm ci
.venv/bin/python -m pytest -q
node --check app/static/app.js
npm test
npx playwright install chromium --only-shell
npm run test:browser
```

Real slskd contract tests run against an isolated Compose stack. Public Soulseek
registration/login checks are opt-in through the manual workflow, not on pushes.

## License

AGPL-3.0-only. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
