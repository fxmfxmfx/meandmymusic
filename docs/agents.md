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
| `apply PLAN_UUID [--wait]` | Apply a reviewed plan by copying and tagging |
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
sources need a new preview. Originals remain in Inbox.

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
