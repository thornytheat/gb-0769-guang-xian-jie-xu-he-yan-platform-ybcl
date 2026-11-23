import hashlib
import json
from datetime import datetime, timezone
from time import perf_counter

from app.config import SPEC


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(material, computed, started):
    sources = {key: digest(value.encode()) for key, value in material['traces'].items()}
    settings = computed['calibration']['settings']
    # 仪表信息属核验留档元数据：迟到的复检信息补登到同一件，不改变件号，也不重复登记。
    identifier = digest(canonical([SPEC, sources, settings]))
    item = {'inspection_id': identifier, 'rule': SPEC, 'traces_sha256': sources, 'settings': settings,
            'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'duration_ms': round(1000 * (perf_counter() - started)), 'computed': computed}
    if material.get('metrology'):
        item['metrology'] = material['metrology']
    return item
