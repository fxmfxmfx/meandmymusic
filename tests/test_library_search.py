import os

import pytest


def put(root, path):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"audio")
    return target


def test_search_finds_files_and_folders_at_every_depth_and_returns_full_paths(auth, settings):
    put(settings.library, 'Artist/Album/CD1/01 ТРЕК.flac')
    put(settings.library, 'Another/Collection/02 трек.mp3')
    (settings.library / 'Трек album').mkdir()
    put(settings.inbox, 'трек-inbox.flac')
    response = auth.get('/api/library/search', params={'q': '  трек '})
    assert response.status_code == 200
    data = response.json()
    assert data['query'] == 'трек'
    assert {e['path'] for e in data['entries']} == {
        'Трек album', 'Artist/Album/CD1/01 ТРЕК.flac', 'Another/Collection/02 трек.mp3'}
    assert next(e for e in data['entries'] if e['directory'])['size'] is None
    assert all(e['audio'] for e in data['entries'] if not e['directory'])
    assert data['next_offset'] is None


def test_search_paginates_duplicate_names_without_losing_their_paths(auth, settings):
    for folder in ['C', 'A', 'B']:
        put(settings.library, f'{folder}/Song.flac')
        put(settings.library, f'{folder}/Disc/Song.flac')
    paths, offset = [], 0
    while offset is not None:
        data = auth.get('/api/library/search', params={'q': 'song', 'limit': 2, 'offset': offset}).json()
        assert len(data['entries']) == 2
        paths.extend(e['path'] for e in data['entries'])
        offset = data['next_offset']
    assert paths == ['A/Song.flac', 'A/Disc/Song.flac', 'B/Song.flac', 'B/Disc/Song.flac', 'C/Song.flac', 'C/Disc/Song.flac']
    assert len(set(paths)) == 6
    assert auth.get('/api/library/search', params={'q': 'song', 'offset': 20}).json()['entries'] == []


def test_search_excludes_hidden_staging_symlinks_and_special_files(auth, settings):
    put(settings.library, '.hidden/secret.flac')
    put(settings.library, 'Artist/.secret.flac')
    put(settings.library, '.mmm-upload-test/secret.flac')
    outside = put(settings.inbox, 'secret.flac')
    (settings.library / 'secret-link.flac').symlink_to(outside)
    (settings.library / 'secret-directory').symlink_to(settings.inbox, target_is_directory=True)
    os.mkfifo(settings.library / 'secret-pipe.wav')
    assert auth.get('/api/library/search', params={'q': 'secret'}).json()['entries'] == []


@pytest.mark.parametrize('params', [{'q': ''}, {'q': 'a' * 301}, {'q': 'song', 'limit': 501},
                                  {'q': 'song', 'offset': -1}, {'q': 'song', 'limit': 0}])
def test_search_rejects_invalid_bounds(auth, params):
    assert auth.get('/api/library/search', params=params).status_code == 422


def test_search_requires_login_and_reports_unreadable_folders(client, auth, monkeypatch):
    from app.files import search_library
    from pathlib import Path

    def inaccessible(root, **options):
        options['onerror'](PermissionError())
        return iter(())

    monkeypatch.setattr('app.files.os.walk', inaccessible)
    assert auth.get('/api/library/search', params={'q': ' '}).status_code == 400
    assert search_library(Path('/unreadable'), 'song')['unreadable_folders'] == 1
    auth.post('/api/logout')
    assert client.get('/api/library/search', params={'q': 'song'}).status_code == 401
