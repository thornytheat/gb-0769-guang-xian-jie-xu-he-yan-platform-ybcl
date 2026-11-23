import pytest

from app.errors import InspectionError
from app.modules.metrology.clock import parse_instant
from app.modules.metrology.service import evaluate, register_meter

WINDOW = [{'start': '2026-11-01T00:00+08:00', 'end': '2026-11-05T00:00+08:00'}]


def payload(meter='MA', end='a', windows=None):
    return {'meter_id': meter, 'end': end, 'out_of_tolerance_windows': windows or WINDOW}


def test_window_requires_timezone():
    with pytest.raises(InspectionError, match='TIMEZONE_REQUIRED'):
        register_meter(payload(windows=[{'start': '2026-11-01T00:00', 'end': '2026-11-05T00:00+08:00'}]))


@pytest.mark.parametrize('patch, code', [
    ({'meter_id': '  '}, 'METER_ID_REQUIRED'),
    ({'end': 'c'}, 'BAD_METER_END'),
    ({'out_of_tolerance_windows': []}, 'WINDOW_REQUIRED'),
])
def test_meter_registration_validation(patch, code):
    with pytest.raises(InspectionError, match=code):
        register_meter({**payload(), **patch})


def test_bad_window_timestamps_and_order():
    with pytest.raises(InspectionError, match='BAD_WINDOW_START'):
        register_meter(payload(windows=[{'start': 'nope', 'end': '2026-11-05T00:00+08:00'}]))
    with pytest.raises(InspectionError, match='BAD_WINDOW_RANGE'):
        register_meter(payload(windows=[{'start': '2026-11-06T00:00+08:00', 'end': '2026-11-05T00:00+08:00'}]))


def test_overlapping_windows_rejected_but_adjacent_allowed():
    with pytest.raises(InspectionError, match='OVERLAPPING_WINDOW'):
        register_meter(payload(windows=[
            {'start': '2026-11-01T00:00+08:00', 'end': '2026-11-05T00:00+08:00'},
            {'start': '2026-11-04T00:00+08:00', 'end': '2026-11-06T00:00+08:00'}]))
    meter = register_meter(payload(windows=[
        {'start': '2026-11-01T00:00+08:00', 'end': '2026-11-05T00:00+08:00'},
        {'start': '2026-11-05T00:01+08:00', 'end': '2026-11-06T00:00+08:00'}]))
    assert len(meter['windows']) == 2


def test_timestamps_normalised_to_utc_seconds():
    meter = register_meter(payload())
    assert meter['windows'] == [{'start': '2026-10-31T16:00:00+00:00', 'end': '2026-11-04T16:00:00+00:00'}]
    assert parse_instant('2026-11-05T00:00Z').utcoffset().total_seconds() == 0


def _record(client, **metrology):
    body = {'sample': 'clean'}
    if metrology:
        body['metrology'] = metrology
    return client.post('/api/inspections', json=body).json['inspection_id']


def _register(client, meter, end, windows=None):
    return client.post('/api/meters', json=payload(meter, end, windows or WINDOW))


def test_legacy_record_without_meter_info_is_held(client):
    iid = _record(client)
    verdict = client.get(f'/api/inspections/{iid}/handover').json
    assert verdict['can_handover'] is False
    assert verdict['hold_codes'] == ['METER_INFO_MISSING']
    assert {item['end'] for item in verdict['ends']} == {'a', 'b'}
    assert all(item['hold'] for item in verdict['ends'])


def test_meter_registration_is_idempotent(client):
    first = _register(client, 'MA', 'a')
    second = _register(client, 'MA', 'a')
    assert (first.status_code, first.json['created'], second.status_code, second.json['created']) == (201, True, 200, False)
    assert len(client.get('/api/meters').json['meters']) == 1


def test_later_recheck_appends_window_without_overwriting(client):
    _register(client, 'MA', 'a')
    later = _register(client, 'MA', 'a', [
        {'start': '2026-11-20T00:00+08:00', 'end': '2026-11-22T00:00+08:00'}])
    assert later.json['created'] is False
    meter = next(m for m in client.get('/api/meters').json['meters']
                 if m['meter_id'] == 'MA' and m['end'] == 'a')
    assert len(meter['windows']) == 2
    assert meter['windows'][0]['start'] == '2026-10-31T16:00:00+00:00'


def test_overlapping_append_is_rejected_and_store_kept(client):
    _register(client, 'MA', 'a')
    response = _register(client, 'MA', 'a', [
        {'start': '2026-11-04T00:00+08:00', 'end': '2026-11-08T00:00+08:00'}])
    assert (response.status_code, response.json['code']) == (422, 'OVERLAPPING_WINDOW')
    meter = client.get('/api/meters').json['meters'][0]
    assert len(meter['windows']) == 1


def test_same_meter_id_on_both_ends_kept_separate(client):
    _register(client, 'DUP', 'a')
    _register(client, 'DUP', 'b', [{'start': '2026-12-01T00:00+08:00', 'end': '2026-12-05T00:00+08:00'}])
    meters = client.get('/api/meters').json['meters']
    assert {(m['meter_id'], m['end']) for m in meters} == {('DUP', 'a'), ('DUP', 'b')}
    iid = _record(client,
                  a={'meter_id': 'DUP', 'measured_at': '2026-11-02T10:00+08:00'},
                  b={'meter_id': 'DUP', 'measured_at': '2026-11-02T10:00+08:00'})
    statuses = {item['end']: item['status'] for item in client.get(f'/api/inspections/{iid}/handover').json['ends']}
    assert statuses == {'a': 'OUT_OF_TOLERANCE_WINDOW', 'b': 'CALIBRATION_CURRENT'}


def test_window_hit_holds_and_clear_measurement_passes(client, clean):
    _register(client, 'MA', 'a')
    _register(client, 'MB', 'b')
    held_body = {'sample': 'clean', 'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-02T10:00+08:00'},
        'b': {'meter_id': 'MB', 'measured_at': '2026-11-02T10:00+08:00'}}}
    held = client.post('/api/inspections', json=held_body).json['inspection_id']
    verdict = client.get(f'/api/inspections/{held}/handover').json
    assert verdict['handover'] == 'HOLD' and verdict['hold_codes'] == ['OUT_OF_TOLERANCE_WINDOW']
    # 另一条链路（不同校准参数 → 不同件号）在失准窗口外实测，可照常交接
    clear_body = {'traces': clean['traces'],
                  'settings': {**clean['settings'], 'length_m': 499},
                  'metrology': {'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'},
                                'b': {'meter_id': 'MB', 'measured_at': '2026-11-10T09:00+08:00'}}}
    cleared = client.post('/api/inspections', json=clear_body).json['inspection_id']
    assert cleared != held
    verdict = client.get(f'/api/inspections/{cleared}/handover').json
    assert verdict['can_handover'] is True and verdict['handover'] == 'CLEARED'


def test_republish_with_conflicting_metrology_is_rejected(client):
    body = {'sample': 'clean', 'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'}}}
    client.post('/api/inspections', json=body)
    conflict = client.post('/api/inspections', json={'sample': 'clean', 'metrology': {
        'a': {'meter_id': 'OTHER', 'measured_at': '2026-11-10T09:00+08:00'}}})
    assert (conflict.status_code, conflict.json['code']) == (409, 'METROLOGY_CONFLICT')
    # 同件重跑且仪表信息一致仍是幂等复用
    repeat = client.post('/api/inspections', json=body)
    assert repeat.status_code == 200 and repeat.json['created'] is False


def test_window_edges_are_inclusive(client, clean):
    _register(client, 'MA', 'a')
    _register(client, 'MB', 'b')
    for index, edge in enumerate(('2026-11-01T00:00+08:00', '2026-11-05T00:00+08:00')):
        body = {'traces': clean['traces'],
                'settings': {**clean['settings'], 'length_m': 499 - index},
                'metrology': {'a': {'meter_id': 'MA', 'measured_at': edge},
                              'b': {'meter_id': 'MB', 'measured_at': '2026-11-10T09:00+08:00'}}}
        iid = client.post('/api/inspections', json=body).json['inspection_id']
        assert client.get(f'/api/inspections/{iid}/handover').json['ends'][0]['status'] == 'OUT_OF_TOLERANCE_WINDOW'


def test_partial_hold_only_flags_affected_end(client):
    _register(client, 'MA', 'a')
    _register(client, 'MB', 'b')
    iid = _record(client,
                  a={'meter_id': 'MA', 'measured_at': '2026-11-02T10:00+08:00'},
                  b={'meter_id': 'MB', 'measured_at': '2026-11-10T09:00+08:00'})
    verdict = client.get(f'/api/inspections/{iid}/handover').json
    assert verdict['can_handover'] is False
    assert [item['status'] for item in verdict['ends']] == ['OUT_OF_TOLERANCE_WINDOW', 'CALIBRATION_CURRENT']


def test_unregistered_meter_is_held(client):
    _register(client, 'MB', 'b')
    iid = _record(client,
                  a={'meter_id': 'GHOST', 'measured_at': '2026-11-10T09:00+08:00'},
                  b={'meter_id': 'MB', 'measured_at': '2026-11-10T09:00+08:00'})
    verdict = client.get(f'/api/inspections/{iid}/handover').json
    assert verdict['hold_codes'] == ['METER_UNREGISTERED']


def test_late_metrology_attaches_to_signed_record_without_duplicate(client):
    iid = _record(client)
    payload_m = {'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'},
        'b': {'meter_id': 'MB', 'measured_at': '2026-11-10T09:05+08:00'}}}
    first = client.post(f'/api/inspections/{iid}/metrology', json=payload_m)
    retry = client.post(f'/api/inspections/{iid}/metrology', json=payload_m)
    assert (first.status_code, first.json['updated'], retry.json['updated']) == (200, True, False)
    # 仍是同一件，不重复登记
    assert len(client.get('/api/inspections').json['inspections']) == 1
    assert client.get(f'/api/inspections/{iid}').json['metrology'] == payload_m['metrology']


def test_attaching_conflicting_metrology_is_rejected(client):
    iid = _record(client, a={'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'})
    conflict = client.post(f'/api/inspections/{iid}/metrology', json={
        'metrology': {'a': {'meter_id': 'OTHER', 'measured_at': '2026-11-10T09:00+08:00'}}})
    assert (conflict.status_code, conflict.json['code']) == (409, 'METROLOGY_CONFLICT')


def test_late_attach_then_window_registration_flips_handover(client):
    # 先签件（无仪表信息）→ 暂缓；补仪表信息并登记窗口后 → 可交接
    iid = _record(client)
    assert client.get(f'/api/inspections/{iid}/handover').json['can_handover'] is False
    client.post(f'/api/inspections/{iid}/metrology', json={'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'},
        'b': {'meter_id': 'MB', 'measured_at': '2026-11-10T09:00+08:00'}}})
    _register(client, 'MA', 'a')
    _register(client, 'MB', 'b')
    verdict = client.get(f'/api/inspections/{iid}/handover').json
    assert verdict['can_handover'] is True
    # 原签发结论不变
    assert client.get(f'/api/certificate/{iid}').json['approved'] is True


def test_handover_keeps_original_verdict(client, dirty):
    iid = client.post('/api/inspections', json={'sample': 'dirty'}).json['inspection_id']
    verdict = client.get(f'/api/inspections/{iid}/handover').json
    certificate = client.get(f'/api/certificate/{iid}').json
    assert verdict['original_verdict']['approved'] == certificate['approved'] is False
    assert verdict['original_verdict']['blockers'] == certificate['blockers'] == ['MISSING_JOINTS', 'LOSS_OVER_LIMIT']
    # 交接判定是追加视图，不回写签发件
    assert 'handover' not in client.get(f'/api/inspections/{iid}').json


def test_metrology_input_validation(client):
    bad = client.post('/api/inspections', json={'sample': 'clean', 'metrology': {
        'a': {'meter_id': '', 'measured_at': '2026-11-10T09:00+08:00'}}})
    assert (bad.status_code, bad.json['code']) == (400, 'BAD_METROLOGY_FIELDS')
    unknown = client.post('/api/inspections/nope/metrology', json={'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'}}})
    assert (unknown.status_code, unknown.json['code']) == (404, 'INSPECTION_NOT_FOUND')


def test_meter_routes_reject_bad_payloads(client):
    assert client.post('/api/meters', json=payload(end='z')).status_code == 422
    assert client.get('/api/meters').json == {'meters': []}


def test_metrology_does_not_change_inspection_identity(client):
    plain = client.post('/api/inspections', json={'sample': 'clean'}).json['inspection_id']
    with_info = client.post('/api/inspections', json={'sample': 'clean', 'metrology': {
        'a': {'meter_id': 'MA', 'measured_at': '2026-11-10T09:00+08:00'}}}).json['inspection_id']
    assert plain == with_info
    stored = client.get(f'/api/inspections/{plain}').json
    assert stored['metrology']['a']['meter_id'] == 'MA'
    # 只补登另一端不产生新件
    again = client.post('/api/inspections', json={'sample': 'clean', 'metrology': {
        'b': {'meter_id': 'MB', 'measured_at': '2026-11-10T09:05+08:00'}}}).json
    assert again['inspection_id'] == plain and again['created'] is False
    assert set(client.get(f'/api/inspections/{plain}').json['metrology']) == {'a', 'b'}
