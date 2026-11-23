from app.views import LABELS


def test_all_panes_render(client):
    home = client.get('/')
    assert '接头设计勾稽'.encode() in home.data and b'F-05' in home.data
    for path, label in LABELS.items():
        entry = client.get('/inspect/' + path)
        assert entry.status_code == 200 and label.encode() in entry.data


def test_loss_pane_includes_keyboard_targets_and_gain(client):
    response = client.get('/inspect/loss')
    assert b'id="event-next"' in response.data and b'data-event="P001"' in response.data
    assert 'A 端假增益'.encode() in response.data


def test_meter_pane_holds_legacy_record_and_registers_window(client):
    iid = client.post('/api/inspections', json={'sample': 'clean'}).json['inspection_id']
    page = client.get(f'/inspect/meter?inspection={iid}')
    assert page.status_code == 200
    assert '暂缓交接'.encode() in page.data and '缺仪表信息暂缓'.encode() in page.data
    assert '原签发结论保持不变'.encode() in page.data

    created = client.post('/inspect/meter', data={
        'action': 'meter', 'meter_id': 'MA', 'end': 'a',
        'window_start': '2026-11-01T00:00+08:00', 'window_end': '2026-11-05T00:00+08:00'})
    assert '已登记失准窗口'.encode() in created.data
    duplicate = client.post('/inspect/meter', data={
        'action': 'meter', 'meter_id': 'MA', 'end': 'a',
        'window_start': '2026-11-01T00:00+08:00', 'window_end': '2026-11-05T00:00+08:00'})
    assert '登记已存在，未重复写入'.encode() in duplicate.data
    assert len(client.get('/api/meters').json['meters']) == 1


def test_meter_pane_late_attach_clears_handover(client):
    iid = client.post('/api/inspections', json={'sample': 'clean'}).json['inspection_id']
    client.post('/api/meters', json={'meter_id': 'MA', 'end': 'a',
                                     'out_of_tolerance_windows': [
                                         {'start': '2026-11-01T00:00+08:00',
                                          'end': '2026-11-05T00:00+08:00'}]})
    client.post('/api/meters', json={'meter_id': 'MB', 'end': 'b',
                                     'out_of_tolerance_windows': [
                                         {'start': '2026-11-01T00:00+08:00',
                                          'end': '2026-11-05T00:00+08:00'}]})
    client.post(f'/inspect/meter?inspection={iid}', data={
        'action': 'metrology', 'inspection': iid,
        'a_meter_id': 'MA', 'a_measured_at': '2026-11-10T09:00+08:00',
        'b_meter_id': 'MB', 'b_measured_at': '2026-11-10T09:00+08:00'})
    page = client.get(f'/inspect/meter?inspection={iid}').text
    assert '可交接 · CLEARED' in page and '仪表合格可交接' in page
    # 签发页同时保留原结论并给出当前交接判定
    certificate = client.get(f'/inspect/certificate?inspection={iid}').text
    assert '原签发结论' in certificate and '现在能否交接' in certificate


def test_meter_pane_surfaces_validation_error(client):
    response = client.post('/inspect/meter', data={
        'action': 'meter', 'meter_id': 'MA', 'end': 'a',
        'window_start': 'x', 'window_end': 'y'})
    assert response.status_code == 200 and 'BAD_WINDOW_START'.encode() in response.data
