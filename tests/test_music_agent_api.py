from uuid import uuid4

from app.music_agent_proxy import MusicAgentProxy


def test_unconfigured_assistant_status_and_request(auth):
    assert auth.get('/api/music-ai').json() == {
        'configured': False, 'available': False, 'model_state': 'unavailable', 'tasks': []}
    assert auth.post('/api/music-ai/tasks', json={'request': 'Artist Album'}).status_code == 503


def test_assistant_routes_trim_validate_and_forward_only_music_request(auth, monkeypatch):
    task_id = str(uuid4())
    calls = []
    async def submit(self, request):
        calls.append(request)
        return {'id': task_id, 'request': request, 'phase': 'start', 'status': 'queued'}
    async def resume(self, key):
        calls.append(key)
        return {'id': key, 'phase': 'downloading', 'status': 'running'}
    monkeypatch.setattr(MusicAgentProxy, 'submit', submit)
    monkeypatch.setattr(MusicAgentProxy, 'resume', resume)
    response = auth.post('/api/music-ai/tasks', json={'request': '  Artist Album  '})
    assert response.status_code == 202 and calls == ['Artist Album']
    assert auth.post('/api/music-ai/tasks', json={'request': ' '}).status_code == 400
    assert auth.post('/api/music-ai/tasks', json={'request': 'x' * 2001}).status_code == 422
    assert auth.post(f'/api/music-ai/tasks/{task_id}/resume').status_code == 202
    assert calls[-1] == task_id
    assert auth.post('/api/music-ai/tasks/not-a-uuid/resume').status_code == 422


def test_assistant_requires_session_and_request_protection(client):
    assert client.get('/api/music-ai').status_code == 401
    assert client.post('/api/music-ai/tasks', json={'request': 'Artist'}).status_code == 401
