# Third-party components

me&my music is original application code licensed under AGPL-3.0-only.

| Component | Use | Upstream license |
| --- | --- | --- |
| [slskd](https://github.com/slskd/slskd) | Separate container, accessed through its HTTP API; no source vendored | AGPLv3 with upstream additional terms; see [upstream LICENSE](https://github.com/slskd/slskd/blob/master/LICENSE) |
| [cryptography](https://github.com/pyca/cryptography) | Fernet authenticated encryption and scrypt key derivation | Apache-2.0 OR BSD-3-Clause |
| [beets](https://github.com/beetbox/beets) | Installed Python dependency for matching, metadata and cataloging | MIT |
| [Mutagen](https://github.com/quodlibet/mutagen) | Installed Python dependency for audio metadata inspection | GPL-2.0-or-later |
| [FastAPI](https://github.com/fastapi/fastapi) | Python web framework | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI server | BSD-3-Clause |
| [HTTPX](https://github.com/encode/httpx) | HTTP client | BSD-3-Clause |

Package distributions retain their own license and copyright notices, including transitive dependencies. The slskd image retains the upstream project name, interface, notices and license; this application does not rebrand or modify it.

[MusicBrainz](https://musicbrainz.org/) is the metadata source. A release lookup is not a guarantee that local files represent that release: the owner reviews the beets match before import. [Navidrome](https://www.navidrome.org/) is an optional external music server, not bundled with this project.
