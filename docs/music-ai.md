# Local music agent

The optional `music-ai` CLI uses the authenticated me&my music HTTP API and a
local llama.cpp model. It needs API credentials only. It does not use SSH or
access CT105 as root. The model service receives no manager credentials and
exposes its API only on `127.0.0.1:8081`.

## Install inside CT111

Run `scripts/install_music_ai.sh` as root from a checkout or copied project
snapshot containing the three agent modules. The installer copies only
`app/__init__.py`, `app/music_agent.py` and `app/music_agent_core.py` to
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
least 95% match confidence, has a complete preview, and has cleanup disabled.
The score is a ranking signal, not a probability. Inbox originals remain. An
artist-only request may select one album only from verified releases; ambiguous
results stop for review. Ambiguous matches, incomplete previews and failed checks
stop automation and require manual review. These conditions do not authorize
permanent deletion or other actions beyond the API workflow.

The model extracts the request and selects a release from real MusicBrainz
results. The application groups exact remote paths by peer and album folder,
checks the FLAC track count, then ranks sources by free slots, shortest queue
and highest reported speed. Reported availability is not a guarantee that a
peer will begin transferring. Format checks use `.flac` filenames; the agent
does not prove lossless provenance or perform audio fingerprint recognition.
MusicBrainz matching and preview checks run after the download.

The model runs on CPU with a single parallel request and an 8192-token context;
searches and decisions can take several minutes. Check the service with
`systemctl status music-ai-llm` and run `music-ai doctor` after provisioning the
config. The service is enabled and started by installation even when the CLI
config has not yet been created.
