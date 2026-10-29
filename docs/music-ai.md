# Local music agent

The optional `music-ai` CLI uses the authenticated me&my music HTTP API and a
local llama.cpp model. It needs API credentials only. It does not use SSH or
access CT105 as root. The model service receives no manager credentials and
exposes its API only on `127.0.0.1:8081`.

## Web requests

Open **Music assistant** in the manager, enter an artist or album and select
**Find and import**. Requests run in the background; closing the page does not
stop them. The page shows the selected album, source, download progress and
import outcome. An artist-only request, such as `madk1d`, processes verified
albums and EPs. `Download all albums of Artist` selects the same mode. Each
album has its own progress and result; a missing source does not stop the others.

Requests that need a decision stop for review instead of
guessing. **Retry** resumes a saved request without restarting completed work.

The model loads automatically for an inference request. After five seconds
without inference work, llama.cpp unloads its weights and KV cache. Downloads,
matching and import continue without the model in memory. Status polling does
not wake it. A small model HTTP process remains available for the next request.

The optional `music-ai-web` service accepts requests from the manager over a
private network. Create `/etc/music-ai/web.env` as a root-owned mode 0600 file:

```dotenv
MUSIC_AI_BIND=AGENT_PRIVATE_ADDRESS
MUSIC_AI_PORT=8082
MUSIC_AI_ALLOWED_CLIENTS=MANAGER_PRIVATE_ADDRESS
MUSIC_AI_WEB_TOKEN=LONG_RANDOM_SHARED_TOKEN
```

Use a random token of at least 32 characters. In the manager's `.env`, configure
`MUSIC_AI_URL=http://AGENT_PRIVATE_ADDRESS:8082` and the same token as
`MUSIC_AI_TOKEN`. Recreate manager after changing its environment. The browser
uses the manager's existing session and never receives this token. Agent API
access is limited by both the token and the source address allowlist.

The installer enables `music-ai-web` when its environment file exists. Otherwise
run `systemctl enable --now music-ai-web` after provisioning it. The manager's
assistant page reports an unconfigured service until both optional settings are
provided.

## Install inside CT111

Run `scripts/install_music_ai.sh` as root from a checkout or copied project
snapshot containing the agent modules. The installer copies only
`app/__init__.py`, `app/music_agent.py`, `app/music_agent_core.py` and
`app/music_agent_server.py`, `app/music_agent_batch.py`,
`app/music_agent_catalog.py` and `app/music_agent_schema.py` to
`/opt/music-ai/app`, creates a dedicated venv with `httpx==0.28.1` constrained
by the project's `requirements.lock`, and installs
`/usr/local/bin/music-ai`. Debian needs `python3-venv` installed. It refuses
unrelated files in `/opt/music-ai` and refuses symlinks at sensitive paths. It
does not run an acquisition task.

The llama.cpp CPU server runs as the unprivileged `music-llm` account. The
installer hardlinks `/root/models/nemotron-bolt-q4.gguf` to
`/var/lib/music-llm/model.gguf`; the source and target must be on the same
filesystem. The model remains root-owned and mode 0644. An existing target is
accepted only when it is the same inode. The service has no shell tools, no
manager credentials, and no network or firewall changes are made.

Provision `/etc/music-ai/config.json` separately as root, mode 0600, with:

```json
{"manager_url":"http://MANAGER_ADDRESS:8080","password":"API_PASSWORD","model_url":"http://127.0.0.1:8081"}
```

Create the config securely; the installer does not invent, display or write a
password. Set directory permissions to root:root 0700. The CLI also supports
`MUSIC_AI_CONFIG` and `MUSIC_AI_STATE` for alternate config and task locations.

## Commands

```text
music-ai "Find the studio album X by Artist"
music-ai run "Find the studio album X by Artist"
music-ai plan "Find albums by Artist"
music-ai resume TASK_ID
music-ai resume TASK_ID --plan
music-ai status [TASK_ID]
music-ai doctor
```

`run` (also the default command) may continue through an authorized workflow.
`plan` performs real searches and model decisions but never queues downloads or
imports. `resume TASK_ID --plan` continues planning without downloading.
Tasks are durable under `/var/lib/music-ai/tasks`; ordinary `resume` continues
the saved task and may begin its download/import steps when their checks pass.
The agent does not poll a model forever.

Automatic import is limited to an exact recommended edition that matches at
least 95% match confidence and has a complete preview. Agent previews explicitly
keep Inbox originals, even when cleanup is enabled for other imports.
The score is a ranking signal, not a probability. Inbox originals remain. An
artist-only request uses a bounded MusicBrainz catalog of albums and EPs, not
singles. Catalog discovery is limited to 1,000 editions and 50 albums; exceeding
these limits requires a narrower request before downloads start. Repeated
editions are grouped, and albums share one initial Soulseek search. A batch can
use a complete, unambiguous recommended edition of the same artist, title and
track count. Ambiguous matches, incomplete previews and failed checks
stop automation and require manual review. These conditions do not authorize
permanent deletion or other actions beyond the API workflow.

The model extracts the request and selects a release from real MusicBrainz
results. The application groups exact remote paths by peer and album folder,
checks the FLAC track count, then ranks sources by free slots, shortest queue
and highest reported speed. Reported availability is not a guarantee that a
peer will begin transferring. Format checks use `.flac` filenames; the agent
does not prove lossless provenance or perform audio fingerprint recognition.
MusicBrainz matching and preview checks run after the download. New downloads
reserve the remaining bytes of existing transfers and leave 256 MiB free in
Inbox. A space refusal remains retryable once space is available.

If the combined artist/album search finds no complete source, the agent tries
an extended search using the album title. Artist and album checks still apply
to the returned paths. Searches with no peer replies report `no_replies`, rather
than claiming the album has no FLAC release. Resuming a task with empty saved
sources performs a new bounded search; it does not reuse the empty result forever.

The model runs on CPU with a single parallel request and an 8192-token context;
searches and decisions can take several minutes. Check the service with
`systemctl status music-ai-llm` and run `music-ai doctor` after provisioning the
config. The service is enabled and started by installation even when the CLI
config has not yet been created.
