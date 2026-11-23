from flask import Blueprint, current_app, jsonify, request

from .errors import InspectionError
from .input import _metrology, request_material
from .modules.archive.service import archive
from .modules.archive.storage import attach_metrology, get, recent
from .modules.certificate.service import certify
from .modules.metrology import storage as meter_store
from .modules.metrology.service import evaluate, register_meter
from .pipeline import run

api = Blueprint('fiber_api', __name__, url_prefix='/api')


def calculate(stage):
    return run(request_material(), stage)[stage]


@api.post('/trace')
def trace():
    return calculate('trace')


@api.post('/calibration')
def calibration():
    return calculate('calibration')


@api.post('/pairs')
def pairs():
    return calculate('pairs')


@api.post('/loss')
def loss():
    return calculate('loss')


@api.post('/design')
def design():
    return calculate('design')


@api.post('/inspections')
def submit():
    report, fresh = archive(current_app.config['DATABASE'], request_material())
    return {'inspection_id': report['inspection_id'], 'created': fresh, 'record': report}, 201 if fresh else 200


@api.get('/inspections')
def list_inspections():
    return {'inspections': recent(current_app.config['DATABASE'])}


@api.get('/inspections/<identifier>')
def inspection(identifier):
    return get(current_app.config['DATABASE'], identifier)


@api.post('/inspections/<identifier>/metrology')
def inspection_metrology(identifier):
    """迟到复检信息追到已签件；同一载荷重试不重复登记。"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InspectionError('BAD_METROLOGY_FIELDS', status=400)
    normalized = _metrology(body)
    if not normalized:
        raise InspectionError('BAD_METROLOGY_FIELDS', status=400)
    record, updated = attach_metrology(current_app.config['DATABASE'], identifier, normalized)
    return {'inspection_id': identifier, 'updated': updated, 'record': record}, 200


@api.get('/certificate/<identifier>')
def certificate(identifier):
    return certify(inspection(identifier))


@api.get('/export/<identifier>')
def export(identifier):
    response = jsonify(certificate(identifier))
    response.headers['Content-Disposition'] = f'attachment; filename="splice-{identifier[:12]}.json"'
    return response


# ---- 仪表复校（失准窗口只追加） ---------------------------------------------

@api.post('/meters')
def register():
    meter = register_meter(request.get_json(silent=True))
    saved, created = meter_store.save_meter(current_app.config['DATABASE'], meter)
    return {'meter': saved, 'created': created}, 201 if created else 200


@api.get('/meters')
def meters():
    return {'meters': meter_store.list_meters(current_app.config['DATABASE'])}


@api.get('/inspections/<identifier>/handover')
def handover(identifier):
    record = get(current_app.config['DATABASE'], identifier)
    return evaluate(record, meter_store.list_meters(current_app.config['DATABASE']))
