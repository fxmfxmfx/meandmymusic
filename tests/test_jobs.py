"""Task state transitions must survive cancellation, polling and cleanup races."""
import sqlite3
import threading
import time
from uuid import uuid4

from app.store import Store
from conftest import RELEASE
from test_import import wait_job


def test_reject_preview_prevents_import_and_cleanup_keeps_polling_record(auth, settings, album, match):
    job_id = auth.post('/api/imports/preview', json={'path': 'Test Album', 'release_id': RELEASE}).json()['id']
    assert wait_job(auth, job_id)['status'] == 'ready'
    assert auth.post(f'/api/jobs/{job_id}/cancel').status_code == 200
    assert auth.get(f'/api/jobs/{job_id}').json()['status'] == 'cancelled'
    assert auth.post(f'/api/imports/{job_id}/apply').status_code == 409
    assert auth.delete('/api/jobs').json() == {'cleared': 1}
    assert auth.get('/api/jobs').json() == []
    assert auth.get(f'/api/jobs/{job_id}').json()['status'] == 'cancelled'
    assert not list(settings.library.rglob('*.wav'))
    assert len(list(album.glob('*.wav'))) == 2


def test_running_preview_cannot_resurrect_after_cancel_and_queued_work_is_skipped(auth, settings, monkeypatch):
    for name in ['First', 'Second']:
        (settings.inbox / name).mkdir()
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(settings, path, release_id):
        calls.append(path)
        started.set()
        assert release.wait(5)
        return {'created': time.time(), 'rows': []}
    monkeypatch.setattr('app.main.preview', blocked)
    try:
        first = auth.post('/api/imports/preview', json={'path':'First', 'release_id':RELEASE}).json()['id']
        assert started.wait(2)
        second = auth.post('/api/imports/preview', json={'path':'Second', 'release_id':RELEASE}).json()['id']
        assert auth.post(f'/api/jobs/{first}/cancel').status_code == 200
        assert auth.post(f'/api/jobs/{second}/cancel').status_code == 200
        assert auth.delete('/api/jobs').json()['cleared'] == 2
    finally:
        release.set()
    # Lifespan will also join the worker; finish_job refuses a cancelled record.
    store = auth.app.state.store
    assert not store.finish_job(first, 'ready', result={'rows':[]})
    assert store.get_job(first)['status'] == 'cancelled'
    assert store.get_job(second)['status'] == 'cancelled'
    assert calls == ['First']


def test_cleanup_preserves_active_and_review_tasks_and_expired_previews_are_unusable(auth):
    store = auth.app.state.store
    keep = []
    for kind, status in [('preview','queued'), ('preview','running'), ('preview','ready'), ('import','running'), ('preview','applying')]:
        key = store.new_job(kind)
        store.update_job(key, status, result={'created':time.time(), 'rows':[]})
        keep.append(key)
    expired = store.new_job('preview')
    store.update_job(expired, 'ready', result={'created':time.time()-3601, 'rows':[]})
    done = store.new_job('import')
    store.update_job(done, 'done', result={'files':1})
    assert auth.delete('/api/jobs').json()['cleared'] == 2
    assert {j['id'] for j in auth.get('/api/jobs').json()} == set(keep)
    assert store.get_job(expired)['status'] == 'expired'
    assert auth.post(f'/api/imports/{expired}/apply').status_code == 409
    assert auth.post(f'/api/jobs/{keep[3]}/cancel').status_code == 409
    assert auth.post(f'/api/jobs/{keep[4]}/cancel').status_code == 409
    assert auth.post(f'/api/jobs/{uuid4()}/cancel').status_code == 404


def test_claim_and_reject_are_mutually_exclusive(settings):
    store = Store(settings.state)
    first = store.new_job('preview')
    store.update_job(first, 'ready', result={'created':time.time()})
    assert store.claim_plan(first)
    assert not store.cancel_job(first)
    second = store.new_job('preview')
    store.update_job(second, 'ready', result={'created':time.time()})
    assert store.cancel_job(second)
    assert not store.claim_plan(second)


def test_existing_store_migrates_without_losing_history(settings):
    path = settings.state / 'app.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, created REAL, kind TEXT, status TEXT, result TEXT, error TEXT)')
        db.execute("INSERT INTO jobs VALUES ('existing',?,'preview','ready',?,NULL)", (time.time(), '{"rows":[]}'))
    store = Store(settings.state)
    assert store.get_job('existing')['status'] == 'ready'
    assert store.new_job('preview')
    assert len(store.jobs()) == 2
