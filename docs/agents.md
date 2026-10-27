# Using the music library through an agent

`bin/mmm` uses the same authenticated HTTP API as the browser. It does not need
filesystem access or SSH to the music server. Configure `MMM_URL` and
`MMM_PASSWORD` in the agent environment. A GitHub token is not required.

## Commands

| Command | Purpose |
| --- | --- |
| `status` | Service configuration and slskd API availability |
| `soulseek-status` | Confirmed Soulseek network login status |
| `soulseek-login` / `soulseek-register` | Sign in/register and await server confirmation |
| `soulseek-logout` / `soulseek-reconnect` | Disconnect and forget / reconnect a remembered account |
| `ls [PATH] --root library\|inbox` | List files and directories |
| `mkdir PATH --root ...` | Create a folder |
| `mv SOURCE DESTINATION --root ...` | Move or rename within a section |
| `search QUERY --wait 20` | Search Soulseek and collect results |
| `results SEARCH_UUID` | Read search status/results |
| `download --user USER --files FILE.json` | Queue explicitly selected files |
| `downloads` | Download queue |
| `clear-downloads` | Clear finished download records; keep files |
| `releases QUERY` | Text search for MusicBrainz editions |
| `preview INBOX_PATH --release RELEASE_UUID [--wait]` | Match an exact edition and preview changes |
| `preview-copy INBOX_PATH --destination LIBRARY_PATH [--wait]` | Preview an as-is folder copy without MusicBrainz |
| `apply PLAN_UUID [--wait]` | Apply a reviewed plan by copying and tagging |
| `match-all [INBOX_PATH]` | Queue album matching; review results before import |
| `jobs` / `job JOB_UUID [--wait]` | Background task status |
| `cancel-job JOB_UUID` | Cancel/reject a preview; no import occurs |
| `clear-jobs` | Hide finished tasks; keep active tasks and ready previews |
| `history` / `undo EVENT_UUID` | History; undo moves and creation of empty folders |

Place `--url` or `--password-env NAME` before the subcommand to override defaults.
Soulseek credentials use an interactive prompt or `MMM_SOULSEEK_PASSWORD`.
`--no-save` avoids persisting a remembered account. Do not pass passwords as
command-line arguments or publish environment files.

## Download/import workflow

An agent needs the owner's selection criteria and authorized actions. Determine
an actual target album and edition; a new reissue is not necessarily a new album.

1. Use `ls` to inspect the library and identify the target release MBID.
2. Use `search` and `results`. Select a complete album folder from a single peer;
   check formats, track count, sizes, quality and availability.
3. Save the exact returned `filename` and `size` values in a JSON manifest, then
   call `download`. Never invent remote paths or file sizes.
4. Wait for every selected transfer to complete successfully. Do not import an
   incomplete or failed download.
5. Use `ls --root inbox` to find the actual local album folder. Do not assume its
   path from the remote folder name.
6. Run `preview PATH --release MBID --wait`. Review every track, tags, confidence
   and `result.rows` destinations.
7. If the selection is unambiguous and import is authorized, run
   `apply PLAN_UUID --wait`. Otherwise ask the owner to choose an edition.
8. Confirm the import task status is `done`. Report imported file count and
   destination. Navidrome sees the changes on its next scan.

These are separate commands, not a built-in autonomous agent or scheduler.

## API details

`POST /api/login` with `{"password":"..."}` establishes the `mmm_session` cookie.
Every mutating request needs `X-Requested-With: meandmymusic`; the CLI adds it.
Errors contain `detail`. Connection errors additionally expose a stable code,
stage and hint under `error`.

`POST /api/musicbrainz/match` accepts an Inbox `path` and optional `artist`/`album`
corrections. It reads embedded metadata and returns ranked track-level beets
candidates; it does not write files. The CLI `releases` command remains a plain
text search and is not equivalent to metadata matching.

`POST /api/imports/preview` accepts `path` and `release_id`, returning a task ID.
`GET /api/jobs/ID` transitions through `queued`, `running` and `ready` with a plan
in `result`. `POST /api/imports/ID/apply` returns a **new** import task ID. Its final
successful status is `done`; the original preview becomes `applied`.

Previews expire after an hour and verify source SHA-256 fingerprints. Changed
sources need a new preview. Originals remain in Inbox by default. Inspect
`result.delete_source` before apply: a reviewed cleanup-enabled preview removes
the whole source folder after a successful import.

`DELETE /api/files` accepts `{"root":"inbox","path":"Album"}` (or `library`). It
permanently deletes a file or an entire folder, including its contents. The UI
requires confirmation. Agents must have explicit authorization for deletion and
must not infer that permission from authorization to import. Deletion cannot be
undone through History.

`POST /api/jobs/ID/cancel` atomically cancels a queued, running or ready preview.
It conflicts with applying that preview; once either succeeds, the other returns
409. Running preview results are discarded after cancellation. Imports already
writing files cannot be cancelled. Expired previews report `expired`.

`DELETE /api/jobs` hides terminal tasks, returning `{"cleared": N}`. Active tasks
and previews awaiting review remain visible. Cleared IDs stay readable for
polling clients; cleanup does not change operation history or files.

`DELETE /api/soulseek/downloads/completed` clears terminal slskd transfer records
without removing downloaded files. Active transfers remain in the queue.

`POST /api/musicbrainz/match-all` accepts `{"path":""}` for Inbox or a relative
folder. It returns queued job IDs and counts of imported/matched/active skips.
A `match` job becomes `ready` with `{match, plan, source}`. If `plan` is present,
review it and apply the same job ID; otherwise choose a release and create a
preview. Cancelling matching discards pending worker results. File listings in
Inbox include per-entry `processing` counts and match/import badges.

`GET /api/preferences` and `PATCH /api/preferences` read/set the strict boolean
`delete_inbox_after_import` (default false). Cleanup is captured in new previews;
changing the preference before apply returns 409 and requires a new preview.
A successful import reports `cleanup.status` as `kept`, `deleted`, `skipped` or
`failed`; cleanup failure does not mean the Library import failed.

Uploads use an explicit manifest: `POST /api/uploads` with root, destination path,
files `[{"path":"Album/01.flac","size":123}]` and optional directory paths.
Stream each file as an octet-stream body to `PUT /api/uploads/ID/INDEX`, then
`POST /api/uploads/ID/complete` to publish or `DELETE /api/uploads/ID` to cancel.
Every request uses normal session authentication and request protection. Folder
paths are relative to the chosen destination, never operating-system paths.
Conflicts and invalid paths fail without overwriting existing files. Library
uploads do not automatically tag or add entries to the beets catalog.

`POST /api/imports/manual-preview` accepts Inbox `path` and relative Library
`destination` (including the new album folder name). Its ready preview has
`mode: "manual"` and is applied through the same `/api/imports/ID/apply` endpoint.
It preserves all source file bytes, tags, artwork and empty directories. It never
queries MusicBrainz. Existing destinations, changed sources and unsafe paths are
rejected; optional cleanup requires the same reviewed deletion choice.

`GET /api/jobs` provides summaries, all pending reviews/active work and recent
terminal tasks. `GET /api/jobs/ID` returns the full result/preview for review.
