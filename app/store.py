import json
import sqlite3
import time
import uuid


class Store:
    def __init__(self, directory):
        self.path = directory / "app.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created REAL, kind TEXT, data TEXT, undone INTEGER DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created REAL, kind TEXT, status TEXT, result TEXT, error TEXT)")
            db.execute("UPDATE jobs SET status='interrupted', error='Сервер перезапущен. Создайте новый предпросмотр; если импорт уже начался, проверьте папку назначения.' WHERE status IN ('queued','running','applying')")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def event(self, kind, data):
        event_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO events (id, created, kind, data) VALUES (?,?,?,?)", (event_id, time.time(), kind, json.dumps(data)))
        return event_id

    def events(self):
        with self.connect() as db:
            return [{"id": r[0], "created": r[1], "kind": r[2], "data": json.loads(r[3]), "undone": bool(r[4])}
                    for r in db.execute("SELECT * FROM events ORDER BY created DESC LIMIT 100")]

    def new_job(self, kind):
        job_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,'queued',NULL,NULL)", (job_id, time.time(), kind))
        return job_id

    def update_job(self, job_id, status, result=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?, result=?, error=? WHERE id=?",
                       (status, json.dumps(result) if result is not None else None, error, job_id))

    def jobs(self):
        with self.connect() as db:
            return [{"id": r[0], "created": r[1], "kind": r[2], "status": r[3],
                     "result": json.loads(r[4]) if r[4] else None, "error": r[5]}
                    for r in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]

    def get_job(self, job_id):
        with self.connect() as db:
            r = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return {"id": r[0], "created": r[1], "kind": r[2], "status": r[3],
                "result": json.loads(r[4]) if r[4] else None, "error": r[5]} if r else None

    def claim_plan(self, job_id):
        with self.connect() as db:
            return db.execute("UPDATE jobs SET status='applying' WHERE id=? AND kind='preview' AND status='ready'", (job_id,)).rowcount == 1
