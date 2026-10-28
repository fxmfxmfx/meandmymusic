def test_extended_search_uses_extended_slskd_limits(auth, monkeypatch):
    from app.integrations import Soulseek

    calls = []

    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {"id": "search-id"}

    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.post(
        "/api/soulseek/searches",
        json={"query": "  Artist name  ", "mode": "extended"},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "POST",
            "/searches",
            {
                "searchText": "Artist name",
                "searchTimeout": 30000,
                "fileLimit": 10000,
                "responseLimit": 250,
            },
        )
    ]


def test_invalid_search_mode_is_rejected_before_upstream_request(auth, monkeypatch):
    from app.integrations import Soulseek

    calls = []

    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {"id": "search-id"}

    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.post(
        "/api/soulseek/searches",
        json={"query": "Artist name", "mode": "deep"},
    )

    assert response.status_code == 422
    assert calls == []
