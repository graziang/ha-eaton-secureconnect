"""Exercise real HTTP classification without contacting Eaton or an alarm."""
import io
import json
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest

from custom_components.eaton_secureconnect.api import Client, PanelUnavailableError
from custom_components.eaton_secureconnect.session import Session
from test_model_session import fixture


@pytest.mark.parametrize('state', ['installer', 'appAccessDisabled'])
def test_panel_403_keeps_jwt_and_resumes_reads(state):
    opener = Mock()
    client = Client('synthetic', opener=opener)
    panel = fixture()
    blocked = False
    requests = []

    def respond(request, timeout):
        requests.append(request)
        path = request.full_url.split('/m2m-eaton-web/')[1]
        if path == 'rest/mobileUser/jwt/login':
            raw = b'synthetic.jwt.signature'
        elif path == 'rest/pair/all/':
            raw = json.dumps([{'pairingName': 'Test', 'client-token': 'synthetic'}]).encode()
        elif blocked:
            raise HTTPError(request.full_url, 403, 'Forbidden', {},
                            io.BytesIO(json.dumps({'state': state}).encode()))
        else:
            key = {'async/system/': 'system', 'async/system/partitions/': 'partitions',
                   'async/system/partitions/zones/': 'zones', 'async/faults/': 'faults',
                   'async/logs/?$top=20': 'logs'}[path]
            raw = json.dumps(panel[key]).encode()
        return io.BytesIO(raw)

    opener.open.side_effect = respond
    session = Session({'username': 'fake', 'password': 'fake', 'pairing_name': 'Test'}, client)
    session.read()
    blocked = True
    for _ in range(3):
        with pytest.raises(PanelUnavailableError):session.read()
    blocked = False
    assert session.read()['partitions']['1']['state'] == 'unset'
    logins = [r for r in requests if r.method == 'PUT']
    assert len(logins) == 1
    assert logins[0].full_url.endswith('/rest/mobileUser/jwt/login')
    assert requests[-1].get_header('Jwt') == 'synthetic.jwt.signature'
