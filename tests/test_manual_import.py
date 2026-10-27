
import pytest
from beets.library import Item

from app.manual_import import apply_manual, preview_manual
from app.tagger import fingerprint, library
from test_import import wait_job


def test_manual_copy_keeps_audio_tags_artwork_and_empty_directories(auth, settings, album, monkeypatch):
    item = Item.from_path(str(album/'01.wav'))
    item.title, item.artist, item.album = 'Original title', 'Original artist', 'Original album'
    item.write()
    (album/'cover.jpg').write_bytes(b'cover bytes')
    (album/'Empty').mkdir()
    original = {p.relative_to(album).as_posix():fingerprint(p) for p in album.rglob('*') if p.is_file()}
    (settings.library/'Offline Artist').mkdir()
    def no_lookup(*args, **kwargs):
        raise AssertionError('Manual import must not query MusicBrainz')
    monkeypatch.setattr('app.main.match_album', no_lookup)
    monkeypatch.setattr('app.tagger.autotag.tag_album', no_lookup)
    response = auth.post('/api/imports/manual-preview',json={'path':'Test Album','destination':'Offline Artist/Offline Album'})
    job = wait_job(auth,response.json()['id'])
    assert job['status']=='ready',job
    assert job['result']['mode']=='manual' and len(job['result']['rows'])==3
    assert not (settings.library/'Offline Artist/Offline Album').exists()
    imported = wait_job(auth,auth.post(f'/api/imports/{job["id"]}/apply').json()['id'])
    assert imported['status']=='done',imported
    target = settings.library/'Offline Artist/Offline Album'
    assert {p.relative_to(target).as_posix():fingerprint(p) for p in target.rglob('*') if p.is_file()} == original
    assert (target/'Empty').is_dir() and album.exists()
    assert target.stat().st_mode & 0o005 == 0o005, 'Navidrome must be able to read and traverse the copied album folder'
    assert Item.from_path(str(target/'01.wav')).title == 'Original title'
    entry = auth.get('/api/files',params={'root':'inbox'}).json()['entries'][0]
    assert entry['processing']['imported']
    assert entry['processing']['match']=='unmatched'
    assert len(list(library(settings).items())) == 2


def test_manual_copy_cleanup_option_and_conflict_never_remove_sources(auth, settings, album):
    (album/'cover.jpg').write_bytes(b'artwork')
    assert auth.patch('/api/preferences',json={'delete_inbox_after_import':True}).status_code==200
    job = auth.post('/api/imports/manual-preview',json={'path':'Test Album','destination':'Offline Album'}).json()['id']
    assert wait_job(auth,job)['result']['delete_source']
    # A competing directory appearing after preview must block the copy.
    (settings.library/'Offline Album').mkdir()
    (settings.library/'Offline Album/keep').write_bytes(b'existing')
    imported = wait_job(auth,auth.post(f'/api/imports/{job}/apply').json()['id'])
    assert imported['status']=='failed' and album.exists()
    assert (settings.library/'Offline Album/keep').read_bytes()==b'existing'
    job = auth.post('/api/imports/manual-preview',json={'path':'Test Album','destination':'Another Album'}).json()['id']
    assert wait_job(auth,job)['status']=='ready'
    imported = wait_job(auth,auth.post(f'/api/imports/{job}/apply').json()['id'])
    assert imported['status']=='done' and imported['result']['cleanup']['status']=='deleted'
    assert not album.exists() and (settings.library/'Another Album/cover.jpg').read_bytes()==b'artwork'


def test_manual_copy_changed_source_and_symlinks_are_rejected(settings, album):
    plan = preview_manual(settings,'Test Album','Offline Album')
    (album/'new-file.txt').write_text('new content')
    with pytest.raises(ValueError,match='changed'):
        apply_manual(settings,plan)
    assert not (settings.library/'Offline Album').exists()
    (album/'link').symlink_to(settings.state,target_is_directory=True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        preview_manual(settings,'Test Album','Offline Album')


def test_manual_publication_conflict_rolls_back_catalog_and_keeps_external_files(settings, album, monkeypatch):
    plan = preview_manual(settings,'Test Album','Offline Album')
    from app.files import rename_noreplace
    def conflict(source,destination):
        destination.mkdir()
        (destination/'external').write_bytes(b'do not overwrite')
        rename_noreplace(source,destination)
    monkeypatch.setattr('app.manual_import.rename_noreplace',conflict)
    with pytest.raises(FileExistsError):
        apply_manual(settings,plan)
    assert not list(library(settings).items())
    assert not list(settings.library.glob('.mmm-copy-*'))
    assert (settings.library/'Offline Album/external').read_bytes()==b'do not overwrite'
    assert len(list(album.glob('*.wav')))==2


def test_manual_copy_accepts_unreadable_audio_as_is_and_tracks_its_source(auth, settings):
    album = settings.inbox/'Unrecognised Album'
    album.mkdir()
    (album/'Unrecognised.flac').write_bytes(b'original file with unreadable metadata')
    plan = preview_manual(settings,'Unrecognised Album','Unrecognised Album')
    result = apply_manual(settings,plan)
    assert result['manual'] and result['files']==1
    assert (settings.library/'Unrecognised Album/Unrecognised.flac').read_bytes()==(album/'Unrecognised.flac').read_bytes()
    assert auth.get('/api/files',params={'root':'inbox'}).json()['entries'][0]['processing']['imported']


@pytest.mark.parametrize('source,destination',[('../state','Album'),('Test Album','../state'),('Test Album',''),('','Album')])
def test_manual_preview_rejects_root_and_traversal(auth, album, source, destination):
    response=auth.post('/api/imports/manual-preview',json={'path':source,'destination':destination})
    assert response.status_code in {400,422}
