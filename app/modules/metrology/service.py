"""仪表复校与交接暂缓判定。

失准窗口只追加不覆盖：复检确认的失准区间登记后，签发件按其 A/B 两端各自的
仪表与实测时间比对窗口。原核验结论保持不变，本模块只追加当前能否交接的判定。
"""
from datetime import datetime, timezone

from app.errors import InspectionError
from .clock import parse_instant, stamp

ENDS = ('a', 'b')
END_NAMES = {'a': 'A 端', 'b': 'B 端'}


def _text(payload, key, code):
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InspectionError(code, status=422)
    return value.strip()


def _windows(payload):
    raw = payload.get('out_of_tolerance_windows')
    if not isinstance(raw, list) or not raw:
        raise InspectionError('WINDOW_REQUIRED', status=422)
    windows = []
    for item in raw:
        if not isinstance(item, dict):
            raise InspectionError('BAD_WINDOW', status=422)
        start = parse_instant(item.get('start'), 'BAD_WINDOW_START')
        end = parse_instant(item.get('end'), 'BAD_WINDOW_END')
        if end <= start:
            raise InspectionError('BAD_WINDOW_RANGE', status=422)
        windows.append({'start': start, 'end': end})
    windows.sort(key=lambda window: window['start'])
    for earlier, later in zip(windows, windows[1:]):
        if later['start'] < earlier['end']:
            raise InspectionError('OVERLAPPING_WINDOW', status=422)
    return windows


def register_meter(payload, now=None):
    if not isinstance(payload, dict):
        raise InspectionError('JSON_OBJECT_REQUIRED', status=400)
    meter_id = _text(payload, 'meter_id', 'METER_ID_REQUIRED')
    end = _text(payload, 'end', 'METER_END_REQUIRED').lower()
    if end not in ENDS:
        raise InspectionError('BAD_METER_END', status=422)
    windows = _windows(payload)
    now = now or datetime.now(timezone.utc)
    return {'meter_id': meter_id, 'end': end,
            'windows': [{'start': stamp(item['start']), 'end': stamp(item['end'])} for item in windows],
            'registered_at': stamp(now)}


def _within(window, instant):
    start = datetime.fromisoformat(window['start'])
    end = datetime.fromisoformat(window['end'])
    return start <= instant <= end


def latest_windows(meters, meter_id, end):
    """同一仪表同一端多次登记时，取登记时间最新的一份窗口快照。窗口按端隔离。"""
    known = [meter for meter in meters if meter['meter_id'] == meter_id and meter['end'] == end]
    if not known:
        return None
    return max(known, key=lambda meter: meter['registered_at'])['windows']


def evaluate_end(end, evidence, meters):
    """返回该端的仪表核验状态，evidence 可为 None（旧件缺仪表信息）。"""
    name = END_NAMES[end]
    if not evidence or not evidence.get('meter_id') or not evidence.get('measured_at'):
        return {'end': end, 'status': 'METER_INFO_MISSING', 'hold': True,
                'reason': f'{name}旧签发件缺仪表编号或实测时间，无法排除失准窗口，按暂缓交接处理'}
    try:
        measured = parse_instant(evidence['measured_at'])
    except InspectionError:
        return {'end': end, 'status': 'BAD_MEASURED_AT', 'hold': True, 'meter_id': evidence['meter_id'],
                'measured_at': evidence['measured_at'],
                'reason': f'{name}实测时间无法解析，按暂缓交接处理'}
    windows = latest_windows(meters, evidence['meter_id'], end)
    if windows is None:
        return {'end': end, 'status': 'METER_UNREGISTERED', 'hold': True,
                'meter_id': evidence['meter_id'], 'measured_at': stamp(measured),
                'reason': f'{name}仪表 {evidence["meter_id"]} 未登记{name}复校信息，按暂缓交接处理'}
    scoped = [window for window in windows if _within(window, measured)]
    if scoped:
        return {'end': end, 'status': 'OUT_OF_TOLERANCE_WINDOW', 'hold': True,
                'meter_id': evidence['meter_id'], 'measured_at': stamp(measured),
                'windows': scoped,
                'reason': f'{name}实测时间 {stamp(measured)} 落在仪表 {evidence["meter_id"]} 的失准窗口内'}
    return {'end': end, 'status': 'CALIBRATION_CURRENT', 'hold': False,
            'meter_id': evidence['meter_id'], 'measured_at': stamp(measured),
            'reason': f'{name}仪表 {evidence["meter_id"]} 在实测时间处于合格区间'}


def _end_evidence(record, end):
    """从签发件中取该端仪表信息；旧件没有 metrology 节时返回 None。"""
    metrology = record.get('metrology')
    if not isinstance(metrology, dict):
        return None
    evidence = metrology.get(end)
    return evidence if isinstance(evidence, dict) else None


def evaluate(record, meters):
    """对已签发件追加交接判定，不修改原记录与原结论。"""
    from app.modules.certificate.service import certify
    certificate = certify(record)
    ends = [evaluate_end(end, _end_evidence(record, end), meters) for end in ENDS]
    holds = [item for item in ends if item['hold']]
    handover = 'HOLD' if holds else 'CLEARED'
    reason_codes = sorted({item['status'] for item in holds})
    return {
        'inspection_id': record['inspection_id'],
        'original_verdict': {'approved': certificate['approved'], 'blockers': certificate['blockers'],
                             'note': '原签发结论留档不变，仅追加当前交接判定'},
        'handover': handover,
        'can_handover': handover == 'CLEARED',
        'hold_codes': reason_codes,
        'ends': ends,
        'reasons': [item['reason'] for item in holds],
        'meters_registered': len({(m['meter_id'], m['end']) for m in meters}),
    }
