import json
from flask import request

from .config import DATA, SOURCES
from .errors import InspectionError

METROLOGY_KEYS = frozenset({'meter_id', 'measured_at'})


def sample(kind):
    if kind not in ('clean', 'dirty'):
        raise InspectionError('UNKNOWN_TRACE_SET', status=404)
    folder = DATA / kind
    return {'traces': {key: (folder / f'{key}.csv').read_text(encoding='utf-8') for key in SOURCES},
            'settings': json.loads((folder / 'link.json').read_text(encoding='utf-8'))}


def _metrology(body):
    metrology = body.get('metrology')
    if metrology is None:
        return None
    if not isinstance(metrology, dict) or set(metrology) - set(('a', 'b')):
        raise InspectionError('BAD_METROLOGY_FIELDS', status=400)
    normalized = {}
    for end in ('a', 'b'):
        evidence = metrology.get(end)
        if evidence is None:
            continue
        if not isinstance(evidence, dict) or set(evidence) != METROLOGY_KEYS:
            raise InspectionError('BAD_METROLOGY_FIELDS', status=400)
        if not all(isinstance(evidence[key], str) and evidence[key].strip() for key in METROLOGY_KEYS):
            raise InspectionError('BAD_METROLOGY_FIELDS', status=400)
        normalized[end] = {key: evidence[key].strip() for key in METROLOGY_KEYS}
    return normalized or None


def request_material():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InspectionError('JSON_OBJECT_REQUIRED', status=400)
    metrology = _metrology(body)
    if set(body) <= {'sample', 'settings', 'metrology'} and 'sample' in body:
        data = sample(body['sample'])
        if metrology:
            data['metrology'] = metrology
        return {**data, 'settings': body.get('settings', data['settings'])}
    data = body.get('traces')
    if set(body) - {'traces', 'settings', 'metrology'} or not isinstance(data, dict) or set(data) != set(SOURCES):
        raise InspectionError('THREE_TRACES_REQUIRED', status=400)
    if any(not isinstance(text, str) for text in data.values()):
        raise InspectionError('TRACE_TEXT_REQUIRED', status=400)
    if metrology:
        body = {**body, 'metrology': metrology}
    return body
