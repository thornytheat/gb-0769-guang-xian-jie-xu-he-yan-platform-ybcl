from flask import Blueprint, abort, current_app, render_template, request

from .config import SOURCES, SPEC
from .errors import InspectionError
from .input import _metrology, sample
from .modules.archive.service import archive
from .modules.archive.storage import attach_metrology, get, recent
from .modules.certificate.service import certify
from .modules.metrology import storage as meter_store
from .modules.metrology.service import evaluate, register_meter
from .pipeline import run
from .modules.calibration.contract import FIELDS
from .views import CODES, LABELS, half_mm_metres, metres, trace_frames

console = Blueprint('console', __name__)
console.add_app_template_filter(metres, 'metres')
console.add_app_template_filter(half_mm_metres, 'half_mm_metres')


def material(mode):
    if mode == 'custom':
        return {'traces': {key: request.form.get(key, '') for key in SOURCES},
                'settings': {key: request.form.get(key, '') for key in FIELDS}}
    return sample(mode)


@console.route('/', methods=['GET', 'POST'])
@console.route('/inspect/<section>', methods=['GET', 'POST'])
def view(section='design'):
    if section not in LABELS:
        abort(404)
    mode = request.values.get('sample', 'dirty')
    database = current_app.config['DATABASE']
    evidence, frames, entries, data = None, {}, [], {}
    issued, fresh, error, sources = None, False, None, {}
    meters, handover, meter_error = [], None, None
    try:
        if section in ('archive', 'certificate', 'meter'):
            entries = recent(database)
            meters = meter_store.list_meters(database)
            if section == 'archive' and request.method == 'POST':
                sources = material(mode)
                issued, fresh = archive(database, sources)
                entries = recent(database)
            identifier = request.args.get('inspection') or (entries[0]['inspection_id'] if entries else None)
            if section == 'certificate' and identifier:
                record = get(database, identifier)
                evidence = certify(record)
                handover = evaluate(record, meters)
            if section == 'meter':
                if request.method == 'POST':
                    action = request.form.get('action', 'meter')
                    if action == 'meter':
                        payload = {'meter_id': request.form.get('meter_id', ''),
                                   'end': request.form.get('end', ''),
                                   'out_of_tolerance_windows': [
                                       {'start': request.form.get('window_start', ''),
                                        'end': request.form.get('window_end', '')}]}
                        before = sum(len(m['windows']) for m in meters
                                     if m['meter_id'] == payload['meter_id'] and m['end'] == payload['end'])
                        meter, created = meter_store.save_meter(database, register_meter(payload))
                        issued, fresh = meter, created or len(meter['windows']) > before
                        meters = meter_store.list_meters(database)
                    elif action == 'metrology' and identifier:
                        metrology = _metrology({'metrology': {
                            end: {'meter_id': request.form.get(f'{end}_meter_id', ''),
                                  'measured_at': request.form.get(f'{end}_measured_at', '')}
                            for end in ('a', 'b')}})
                        if metrology:
                            attach_metrology(database, identifier, metrology)
                            entries = recent(database)
                if identifier:
                    handover = evaluate(get(database, identifier), meters)
        else:
            sources = material(mode)
            data = run(sources, section)
            if 'calibration' in data:
                frames = trace_frames(data['calibration'])
    except InspectionError as exc:
        if section == 'meter':
            meter_error = exc.code
        else:
            error = exc.code
    return render_template('console.html', section=section, labels=LABELS, codes=CODES, spec=SPEC, mode=mode,
                           data=data, frames=frames, error=error, materials=sources, entries=entries,
                           evidence=evidence, issued=issued, fresh=fresh, fields=FIELDS,
                           meters=meters, handover=handover, meter_error=meter_error)
