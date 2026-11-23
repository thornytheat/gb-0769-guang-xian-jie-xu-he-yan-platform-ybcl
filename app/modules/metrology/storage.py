import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import InspectionError
from .clock import parse_instant, stamp


@contextmanager
def session(database):
    with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS meters ('
            'meter_id TEXT NOT NULL, end TEXT NOT NULL, registered_at TEXT NOT NULL, '
            'document BLOB NOT NULL, PRIMARY KEY (meter_id, end))')
        yield connection


def _no_overlap(windows):
    ordered = sorted(windows, key=lambda window: parse_instant(window['start']))
    for earlier, later in zip(ordered, ordered[1:]):
        if parse_instant(later['start']) < parse_instant(earlier['end']):
            raise InspectionError('OVERLAPPING_WINDOW', status=422)


def save_meter(database, meter):
    """登记失准窗口。同仪表同端重复登记时只追加新窗口，绝不覆盖或删除旧窗口。"""
    with session(database) as db:
        row = db.execute('SELECT document FROM meters WHERE meter_id=? AND end=?',
                         (meter['meter_id'], meter['end'])).fetchone()
        if not row:
            db.execute('INSERT INTO meters VALUES (?, ?, ?, ?)',
                       (meter['meter_id'], meter['end'], stamp(meter['registered_at']),
                        json.dumps(meter, sort_keys=True, ensure_ascii=False).encode()))
            return meter, True
        stored = json.loads(row[0])
        additions = [window for window in meter['windows'] if window not in stored['windows']]
        if not additions:
            return stored, False
        combined = stored['windows'] + additions
        _no_overlap(combined)
        stored['windows'] = sorted(combined, key=lambda window: window['start'])
        db.execute('UPDATE meters SET document=? WHERE meter_id=? AND end=?',
                   (json.dumps(stored, sort_keys=True, ensure_ascii=False).encode(),
                    meter['meter_id'], meter['end']))
        return stored, False


def list_meters(database):
    with session(database) as db:
        rows = db.execute('SELECT document FROM meters ORDER BY meter_id, end').fetchall()
        return [json.loads(row[0]) for row in rows]
