import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.inbox import PROVENANCE, album_paths, snapshot
from app.tagger import apply_import, library, preview
from conftest import RELEASE
from test_import import wait_job


def inbox(auth, path=''):
    return auth.get('/api/files', params={'root':'inbox','path':path}).json()['entries']


def test_import_badges_survive_cleanup_moves_and_detect_source_changes(auth, settings, album, match):
    plan = preview(settings, 'Test Album', RELEASE)
    assert 'processing' in inbox(auth)[0]
    apply_import(settings, plan)
    info = inbox(auth)[0]['processing']
    assert info['imported'] and info['match'] == 'matched'
    assert info['imported_files'] == 2
    assert all(entry['processing']['imported'] for entry in inbox(auth, 'Test Album'))
    assert all(item.get(PROVENANCE) for item in library(settings).items())
    assert auth.delete('/api/jobs').status_code == 200
    assert inbox(auth)[0]['processing']['imported']
    assert auth.post('/api/move', json={'root':'inbox','source':'Test Album','destination':'Renamed'}).status_code == 200
    assert inbox(auth)[0]['processing']['imported']
    event = auth.get('/api/history').json()[0]
    assert auth.post(f'/api/history/{event["id"]}/undo').status_code == 200
    assert inbox(auth)[0]['processing']['imported']
    (album / '02.wav').write_bytes(b'changed')
    assert inbox(auth)[0]['processing']['imported_files'] == 1
    assert not inbox(auth)[0]['processing']['imported']
    assert auth.request('DELETE','/api/files',json={'root':'library','path':'Test Artist'}).status_code == 200
    assert not inbox(auth)[0]['processing']['imported']


def test_matches_cached_until_tracks_change_and_follow_folder_rename(auth, settings, album, match, monkeypatch):
    def strong(settings, source, artist=None, album=None):
        return {'artist':'Artist','album':'Album','tracks':2,'has_tags':True,
                'candidates':[{'id':RELEASE,'confidence':99}], 'recommended_release_id':RELEASE}
    monkeypatch.setattr('app.main.match_album', strong)
    assert auth.post('/api/musicbrainz/match',json={'path':'Test Album'}).status_code == 200
    assert inbox(auth)[0]['processing']['match'] == 'matched'
    assert auth.post('/api/musicbrainz/match',json={'path':'Test Album'}).json()['cached']
    assert auth.post('/api/move',json={'root':'inbox','source':'Test Album','destination':'Renamed'}).status_code == 200
    assert inbox(auth)[0]['processing']['match'] == 'matched'
    (settings.inbox / 'Renamed' / '02.wav').write_bytes(b'changed')
    assert inbox(auth)[0]['processing']['match'] == 'unmatched'


def test_weak_match_is_review_not_identification(auth, album, monkeypatch):
    monkeypatch.setattr('app.main.match_album', lambda *args: {'candidates':[{'id':RELEASE,'confidence':41}], 'recommended_release_id':None})
    assert auth.post('/api/musicbrainz/match',json={'path':'Test Album'}).status_code == 200
    assert inbox(auth)[0]['processing']['match'] == 'review'


def test_match_all_discovers_nested_albums_as_single_jobs_and_requires_review(auth, settings, album, match, monkeypatch):
    multi = settings.inbox / 'Artist' / 'Double Album'
    for disc in ['CD1','CD2']:
        (multi / disc).mkdir(parents=True)
        shutil.copy2(album / '01.wav', multi / disc / '01.wav')
    assert album_paths(settings,'') == ['Artist/Double Album','Test Album']
    def strong(*args):
        return {'artist':'Test Artist','album':'Test Album','tracks':2,'has_tags':False,
                'candidates':[{'id':RELEASE,'confidence':99}], 'recommended_release_id':RELEASE}
    monkeypatch.setattr('app.main.match_album', strong)
    result = auth.post('/api/musicbrainz/match-all',json={'path':''}).json()
    assert result['queued'] == 2
    jobs = [wait_job(auth,job['id']) for job in result['jobs']]
    assert all(job['status']=='ready' and job['kind']=='match' and job['result']['plan'] for job in jobs)
    assert not list(settings.library.rglob('*.wav'))
    again = auth.post('/api/musicbrainz/match-all',json={'path':''}).json()
    assert again['queued'] == 0 and again['skipped_matched'] == 2
    job = auth.post(f'/api/imports/{jobs[0]["id"]}/apply')
    assert wait_job(auth,job.json()['id'])['status'] == 'done'
    assert len(list(settings.library.rglob('*.wav'))) == 2
    assert auth.post('/api/musicbrainz/match-all',json={'path':'../state'}).status_code == 400


def test_batch_cancellation_discards_late_results_and_skips_imported(auth, settings, album, match, monkeypatch):
    apply_import(settings, preview(settings,'Test Album',RELEASE))
    other = settings.inbox / 'Other'
    other.mkdir()
    shutil.copy2(album / '01.wav', other / '01.wav')
    started, finish = threading.Event(), threading.Event()
    def blocked(*args):
        started.set()
        assert finish.wait(5)
        return {'candidates':[], 'recommended_release_id':None}
    monkeypatch.setattr('app.main.match_album', blocked)
    try:
        result = auth.post('/api/musicbrainz/match-all',json={}).json()
        assert result['skipped_imported']==1 and result['queued']==1
        job = result['jobs'][0]['id']
        assert started.wait(2)
        assert auth.post(f'/api/jobs/{job}/cancel').status_code == 200
    finally:
        finish.set()
    # The worker cannot commit a cancelled task's cache, even after network completion.
    store = auth.app.state.store
    assert not store.finish_job(job,'ready',result={},match_record=('Other','digest',{'candidates':[]}))
    assert 'Other' not in store.matches()


def test_cleanup_preference_off_by_default_and_preview_explains_frozen_choice(auth, settings, album, match):
    assert auth.get('/api/preferences').json() == {'delete_inbox_after_import':False}
    assert auth.patch('/api/preferences',json={'delete_inbox_after_import':'yes'}).status_code == 422
    assert auth.patch('/api/preferences',json={'delete_inbox_after_import':True}).status_code == 200
    job = auth.post('/api/imports/preview',json={'path':'Test Album','release_id':RELEASE}).json()['id']
    plan = wait_job(auth,job)
    assert plan['result']['delete_source'] is True
    assert auth.patch('/api/preferences',json={'delete_inbox_after_import':False}).status_code == 200
    assert auth.post(f'/api/imports/{job}/apply').status_code == 409
    assert album.exists() and not list(settings.library.rglob('*.wav'))


def test_cleanup_deletes_only_reviewed_source_after_successful_import(auth, settings, album, match):
    (album / 'cover.jpg').write_bytes(b'artwork')
    auth.patch('/api/preferences',json={'delete_inbox_after_import':True})
    job = auth.post('/api/imports/preview',json={'path':'Test Album','release_id':RELEASE}).json()['id']
    assert wait_job(auth,job)['status'] == 'ready'
    result = auth.post(f'/api/imports/{job}/apply').json()['id']
    imported = wait_job(auth,result)
    assert imported['status'] == 'done', imported
    assert imported['result']['cleanup']['status'] == 'deleted'
    assert not album.exists()
    assert len(list(library(settings).items())) == 2
    assert len(list(settings.library.rglob('*.wav'))) == 2


def test_changed_folder_or_cleanup_failure_keeps_import_success(settings, album, match, monkeypatch):
    plan = preview(settings,'Test Album',RELEASE)
    plan['delete_source'] = True
    (album / 'new-cover.jpg').write_bytes(b'new file')
    result = apply_import(settings,plan)
    assert result['cleanup']['status'] == 'skipped' and album.exists()
    shutil.rmtree(settings.library / 'Test Artist')
    from app.tagger import remove_catalog_paths
    remove_catalog_paths(settings,settings.library / 'Test Artist')
    plan = preview(settings,'Test Album',RELEASE)
    plan['delete_source'] = True
    def fail(path, *args, **kwargs):
        raise PermissionError('test')
    monkeypatch.setattr('app.tagger.shutil.rmtree',fail)
    result = apply_import(settings,plan)
    assert result['cleanup']['status'] == 'failed'
    assert len(list(settings.library.rglob('*.wav'))) == 2 and album.exists()


def test_failed_import_never_deletes_inbox(settings, album, match, monkeypatch):
    plan = preview(settings,'Test Album',RELEASE)
    plan['delete_source'] = True
    from beets.library import Item
    def fail(*args, **kwargs):
        raise OSError('test failure')
    monkeypatch.setattr(Item,'write',fail)
    with pytest.raises(OSError):
        apply_import(settings,plan)
    assert album.exists() and len(list(album.glob('*.wav'))) == 2


def test_all_pending_batch_reviews_remain_visible_beyond_recent_task_limit(auth):
    store = auth.app.state.store
    pending = set()
    for index in range(110):
        job = store.new_job('match',f'Album {index}')
        store.update_job(job,'ready',result={'match':{'candidates':[]},'plan':None})
        pending.add(job)
    for _ in range(105):
        store.update_job(store.new_job('import'),'done',result={'files':1})
    visible = auth.get('/api/jobs').json()
    assert {job['id'] for job in visible if job['status']=='ready'} == pending
