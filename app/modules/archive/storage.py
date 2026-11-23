import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.errors import InspectionError
from .identity import canonical


@contextmanager
def session(database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS inspections (id TEXT UNIQUE NOT NULL, document BLOB NOT NULL)')
        yield connection


def publish(database, manifest):
    with session(database) as db:
        key = manifest['inspection_id']
        changed = db.execute('INSERT INTO inspections VALUES (?, ?) ON CONFLICT(id) DO NOTHING',
                             (key, canonical(manifest))).rowcount
        if changed:
            return json.loads(canonical(manifest)), True
        # 同件重跑：件号只由轨迹与校准参数决定。仪表信息是该件的留档元数据，
        # 只能补登缺失端；对已留档端给出不同仪表/时间属于冲突，拒绝覆盖。
        stored = json.loads(db.execute('SELECT document FROM inspections WHERE id=?', (key,)).fetchone()[0])
        incoming = manifest.get('metrology')
        if incoming:
            existing = stored.get('metrology') or {}
            for end, evidence in incoming.items():
                if end in existing and existing[end] != evidence:
                    raise InspectionError('METROLOGY_CONFLICT', status=409)
            merged = {**incoming, **existing}
            if merged != existing:
                stored['metrology'] = merged
                db.execute('UPDATE inspections SET document=? WHERE id=?', (canonical(stored), key))
        return stored, False


def get(database, identifier):
    with session(database) as db:
        record = db.execute('SELECT document FROM inspections WHERE id=?', (identifier,)).fetchone()
        if not record:
            raise InspectionError('INSPECTION_NOT_FOUND', status=404)
        return json.loads(record[0])


def recent(database):
    with session(database) as db:
        rows = (json.loads(data) for data, in db.execute('SELECT document FROM inspections ORDER BY rowid DESC LIMIT 50'))
        return [{key: row[key] for key in ('inspection_id', 'generated_at', 'rule', 'duration_ms')} for row in rows]


def attach_metrology(database, identifier, metrology):
    """把迟到的仪表/实测信息追到已签件。重试或信息一致时不重复登记。"""
    with session(database) as db:
        row = db.execute('SELECT document FROM inspections WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise InspectionError('INSPECTION_NOT_FOUND', status=404)
        stored = json.loads(row[0])
        existing = stored.get('metrology') or {}
        merged, updated = dict(existing), False
        for end, evidence in metrology.items():
            if end not in merged:
                merged[end] = evidence
                updated = True
            elif merged[end] != evidence:
                raise InspectionError('METROLOGY_CONFLICT', status=409)
        if updated:
            stored['metrology'] = merged
            db.execute('UPDATE inspections SET document=? WHERE id=?', (canonical(stored), identifier))
        return stored, updated
