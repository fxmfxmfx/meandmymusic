import os
from pathlib import Path

import pytest


def begin(auth, root='inbox', files=None, directories=None, path=''):
    return auth.post('/api/uploads',json={'root':root,'path':path,'files':files or [],'directories':directories or []})


@pytest.mark.parametrize('root',['inbox','library'])
def test_upload_nested_folders_and_empty_directories(auth, settings, root):
    response = begin(auth,root,files=[{'path':'Album/CD1/01.flac','size':3},{'path':'Album/cover.jpg','size':0}],directories=['Album','Album/CD1','Album/Empty'])
    assert response.status_code == 200,response.text
    job = response.json()['id']
    assert auth.post(f'/api/uploads/{job}/complete').status_code == 409
    assert auth.put(f'/api/uploads/{job}/0',content=b'abc').status_code == 200
    assert auth.put(f'/api/uploads/{job}/1',content=b'').status_code == 200
    base = settings.inbox if root=='inbox' else settings.library
    assert not (base/'Album').exists()
    assert auth.post(f'/api/uploads/{job}/complete').json()['files'] == 2
    assert (base/'Album/CD1/01.flac').read_bytes() == b'abc'
    assert (base/'Album/Empty').is_dir()
    assert not list(base.glob('.mmm-upload-*'))
    assert auth.get('/api/history').json()[0]['kind'] == 'upload'


@pytest.mark.parametrize('path',['../escape','/absolute','Album/../../escape','Album\\evil','./file',''])
def test_upload_rejects_unsafe_paths(auth, settings, path):
    assert begin(auth,files=[{'path':path,'size':1}]).status_code in {400,422}
    assert not list(settings.inbox.glob('.mmm-upload-*'))


def test_upload_does_not_follow_symlinks_or_overwrite_files(auth, settings):
    (settings.inbox/'existing.flac').write_bytes(b'original')
    assert begin(auth,files=[{'path':'existing.flac','size':1}]).status_code == 409
    (settings.inbox/'outside').symlink_to(settings.state,target_is_directory=True)
    assert begin(auth,files=[{'path':'outside/data','size':1}]).status_code == 400
    assert not (settings.state/'data').exists()


def test_upload_cancellation_and_interrupted_body_do_not_publish(auth, settings):
    job = begin(auth,files=[{'path':'Track.flac','size':3}]).json()['id']
    assert auth.put(f'/api/uploads/{job}/0',content=b'ab').status_code == 400
    assert auth.put(f'/api/uploads/{job}/0',content=b'abcd').status_code == 413
    assert not (settings.inbox/'Track.flac').exists()
    assert auth.delete(f'/api/uploads/{job}').status_code == 200
    assert auth.post(f'/api/uploads/{job}/complete').status_code == 404
    assert not list(settings.inbox.glob('.mmm-upload-*'))


def test_upload_competing_writer_rolls_back_without_overwriting(auth, settings, monkeypatch):
    job = begin(auth,files=[{'path':'Album/one.flac','size':1},{'path':'Album/two.flac','size':1}]).json()['id']
    for i in range(2):
        assert auth.put(f'/api/uploads/{job}/{i}',content=b'x').status_code == 200
    link = os.link
    def competing(source,destination):
        if destination.name=='two.flac':
            destination.write_bytes(b'external file')
        return link(source,destination)
    monkeypatch.setattr('app.uploads.os.link',competing)
    assert auth.post(f'/api/uploads/{job}/complete').status_code == 409
    assert not (settings.inbox/'Album/one.flac').exists()
    assert (settings.inbox/'Album/two.flac').read_bytes() == b'external file'


def test_upload_manifest_conflicts_fail_before_staging(auth, settings):
    for files, dirs in [([{'path':'Album','size':1},{'path':'Album/Track','size':1}],[]),
                        ([{'path':'Track','size':1},{'path':'Track','size':1}],[]),
                        ([{'path':'Album','size':1}],['Album/Empty'])]:
        assert begin(auth,files=files,directories=dirs).status_code == 409
    assert not list(settings.inbox.glob('.mmm-upload-*'))


def test_new_app_cleans_only_journalled_abandoned_staging(settings):
    from app.uploads import Uploads
    first = Uploads(settings)
    first.begin('inbox','',[{'path':'one','size':1}],[])
    unrelated = settings.inbox/'.mmm-upload-user-folder'
    unrelated.mkdir()
    (unrelated/'keep').write_bytes(b'music')
    second = Uploads(settings)
    assert (unrelated/'keep').exists()
    assert list(settings.inbox.glob('.mmm-upload-*')) == [unrelated]
    assert not second.sessions


def test_upload_reports_insufficient_space_before_any_file_transfer(auth, settings, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.uploads.shutil.disk_usage',lambda root:SimpleNamespace(free=1))
    assert begin(auth,files=[{'path':'Big.flac','size':2}]).status_code == 507
    assert not list(settings.inbox.glob('.mmm-upload-*'))
