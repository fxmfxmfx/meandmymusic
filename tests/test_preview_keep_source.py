import time

from conftest import RELEASE


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    raise AssertionError("Job did not finish")


def set_cleanup(client, enabled):
    response = client.patch("/api/preferences", json={"delete_inbox_after_import": enabled})
    assert response.status_code == 200, response.text


def test_keep_source_overrides_enabled_cleanup_for_one_preview(auth, settings, album, match):
    set_cleanup(auth, True)

    response = auth.post("/api/imports/preview", json={
        "path": "Test Album", "release_id": RELEASE, "keep_source": True,
    })
    assert response.status_code == 200, response.text
    preview = wait_job(auth, response.json()["id"])
    assert preview["status"] == "ready", preview
    assert preview["result"]["delete_source"] is False
    assert preview["result"]["keep_source"] is True
    assert auth.get("/api/preferences").json()["delete_inbox_after_import"] is True

    applied = auth.post(f"/api/imports/{preview['id']}/apply")
    assert applied.status_code == 200, applied.text
    result = wait_job(auth, applied.json()["id"])
    assert result["status"] == "done", result
    assert len(list(album.glob("*.wav"))) == 2
    assert auth.get("/api/preferences").json()["delete_inbox_after_import"] is True


def test_cleanup_preference_change_still_rejects_standard_preview(auth, album, match):
    set_cleanup(auth, False)
    response = auth.post("/api/imports/preview", json={"path": "Test Album", "release_id": RELEASE})
    preview = wait_job(auth, response.json()["id"])
    assert preview["status"] == "ready", preview
    assert "keep_source" not in preview["result"]

    set_cleanup(auth, True)
    applied = auth.post(f"/api/imports/{preview['id']}/apply")
    assert applied.status_code == 409
    assert "cleanup setting changed" in applied.json()["detail"]


def test_keep_source_requires_strict_boolean(auth, album, match):
    response = auth.post("/api/imports/preview", json={
        "path": "Test Album", "release_id": RELEASE, "keep_source": "true",
    })
    assert response.status_code == 422


def test_keep_source_marker_does_not_allow_delete_source_plan(auth, album, match):
    set_cleanup(auth, False)
    response = auth.post("/api/imports/preview", json={"path": "Test Album", "release_id": RELEASE})
    preview = wait_job(auth, response.json()["id"])
    assert preview["status"] == "ready", preview
    result = preview["result"]
    result["delete_source"] = True
    result["keep_source"] = True
    with auth.app.state.store.connect() as db:
        import json
        db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result), preview["id"]))
    assert auth.post(f"/api/imports/{preview['id']}/apply").status_code == 409
