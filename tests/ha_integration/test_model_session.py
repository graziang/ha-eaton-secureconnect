import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from custom_components.eaton_secureconnect.api import ProbeError, PanelUnavailableError
from custom_components.eaton_secureconnect.model import normalize, contact_state, fault_state, alarm_state, check_command, EventCursor
from custom_components.eaton_secureconnect.session import Session, AuthError


def fixture():
    # Sanitized real response shape; synthetic names/IDs, no private fixtures.
    return {'system': {'mode':'partitioned'}, 'partitions':[{'id':'1','name':'Area','state':'unset','in-alarm':False}],
        'zones':[{'id':'0','name':'Window','state':'closed','omitted':False}], 'faults':[], 'logs':[],
        'zone_groups':[{'partition':'1','zones':[{'id':'0'}]}]}


def test_discovery_and_unknown():
    data=normalize(fixture())
    assert data['zone_partitions']['0']=='1'
    assert contact_state({'state':'open'}) is True
    assert contact_state({'state':'closed'}) is False
    assert contact_state({'state':'unexpected'}) is None
    assert alarm_state({'state':'fullset','time-to-exit':12})=='arming'
    assert alarm_state({'state':'unset','in-alarm':True})=='triggered'
    assert alarm_state({'state':'partset'}) is None

@pytest.mark.parametrize('change', ['missing','duplicate','bad_type'])
def test_invalid_snapshot_fails(change):
    raw=fixture()
    if change=='missing': del raw['zones']
    if change=='duplicate': raw['zones']*=2
    if change=='bad_type': raw['faults']=None
    with pytest.raises(ProbeError): normalize(raw)

@pytest.mark.parametrize('field,value', [('state','open'),('state','unknown'),('omitted',True),('omitted',None)])
def test_no_unsafe_arm(field,value):
    raw=fixture();raw['zones'][0][field]=value
    with pytest.raises(ProbeError): check_command(raw['partitions'][0],'fullset',raw['zones'],[])


def test_controls_and_faults():
    raw=fixture();area=raw['partitions'][0]
    assert check_command(area,'fullset',raw['zones'],[]) is True
    assert check_command(area,'unset',[],[]) is False
    for zones,faults in (([],[]),(raw['zones'],[{}])):
        with pytest.raises(ProbeError):check_command(area,'fullset',zones,faults)
    area['in-alarm']=True
    with pytest.raises(ProbeError):check_command(area,'unset',[],[])


def test_fault_reports_are_not_the_panel_fault_indicator():
    reports = [{'type': 1, 'ui-string': 'Synthetic setting impediment', 'prevents-set': True}]
    assert fault_state({'1': {'is-fault': False}}, reports) is False
    assert fault_state({'1': {'is-fault': True}}, []) is True
    assert fault_state({'1': {'is-fault': False}, '2': {}}, reports) is None
    assert fault_state({'1': {}}, reports) is None
    assert fault_state({'1': {}}, []) is False
    raw = fixture()
    raw['faults'] = reports
    assert normalize(raw)['faults'] == reports


def test_firmware5_open_zone_impediments_are_not_faults():
    areas = {'1': {}, '2': {}}
    reports = [{'type': '5000', 'prevents-set': True, 'ui-string': 'Synthetic open zone'}]
    assert fault_state(areas, reports) is False
    assert fault_state(areas, [{'type': 5000}]) is False
    assert fault_state(areas, reports + [{'type': 'unexpected'}]) is None
    assert fault_state({'1': {'is-fault': True}}, reports) is True
    # Distinguishing the indication must not bypass existing arming checks.
    raw = fixture()
    with pytest.raises(ProbeError):
        check_command(raw['partitions'][0], 'fullset', raw['zones'], reports)


def test_event_cursor_baseline_dedup_gap_reconnect():
    c=EventCursor()
    assert c.consume([{'id':'1'}])==[]
    assert c.consume([{'id':'2'},{'id':'1'}])==[{'id':'2'}]
    assert c.consume([{'id':'2'},{'id':'1'}])==[]
    assert c.consume([{'id':'99'}])==[] and c.gap
    c.reset()
    assert c.consume([{'id':'100'},{'id':'99'}])==[]


def session():
    client=Mock();client.last_error=None
    client.pairings.return_value=[{'pairingName':'Test','client-token':'synthetic'}]
    client.read_panel.return_value=fixture()
    client.read_partitions.return_value=fixture()['partitions']
    client.read_partition_zones.return_value=fixture()['zones']
    client.read_faults.return_value=[]
    return Session({'application_id':'synthetic','username':'fake','password':'fake','pairing_name':'Test'},client),client


def test_command_timeout_never_retried():
    s,client=session();s.read()
    client.set_partition.side_effect=ProbeError('timeout')
    with pytest.raises(ProbeError,match='Esito incerto'):s.command('1','fullset')
    assert client.set_partition.call_count==1
    assert client.login.call_count==1


def test_auth_renewal_once_and_no_command():
    s,client=session()
    def failed_read(token):
        client.last_error={'http_status':401}
        raise ProbeError('expired')
    client.read_panel.side_effect=failed_read
    with pytest.raises(ProbeError) as error:s.read()
    assert not isinstance(error.value, AuthError)
    assert client.login.call_count==2
    # Repeated coordinator polls do not hammer the login endpoint.
    with pytest.raises(ProbeError):s.read()
    assert client.login.call_count==2
    client.set_partition.assert_not_called()


def test_network_failure_does_not_relogin():
    s,client=session();s.read()
    client.read_panel.side_effect=ProbeError('network')
    with pytest.raises(ProbeError):s.read()
    assert client.login.call_count==1


def open_zone_report():
    return {'type': '5000', 'prevents-set': True, 'prevents-reset': False}


def test_open_zones_in_other_area_do_not_block_closed_target():
    s, client = session()
    s.read()
    client.read_faults.return_value = [open_zone_report()]
    s.command('1', 'fullset')
    client.set_partition.assert_called_once_with('synthetic', '1', 'fullset')
    calls = [c[0] for c in client.method_calls]
    assert calls.index('read_faults') < calls.index('read_partition_zones') < calls.index('set_partition')
    # The same reports never permit open/omitted/unknown TARGET zones.
    for change in ({'state': 'open'}, {'state': 'unknown'}, {'omitted': True}, {'omitted': None}):
        client.set_partition.reset_mock()
        client.read_partition_zones.return_value = [{**fixture()['zones'][0], **change}]
        with pytest.raises(ProbeError, match='area selezionata'):
            s.command('1', 'fullset')
        client.set_partition.assert_not_called()


@pytest.mark.parametrize('report', [
    {}, {'type': 'other'}, {'type': '5000'},
    {**open_zone_report(), 'prevents-reset': True},
    {**open_zone_report(), 'prevents-set': False},
])
def test_unknown_or_global_report_blocks_all_areas(report):
    s, client = session();s.read()
    client.read_faults.return_value = [open_zone_report(), report]
    with pytest.raises(ProbeError, match='guasto centrale'):
        s.command('1', 'fullset')
    client.set_partition.assert_not_called()


def test_explicit_fault_in_other_area_is_still_global_blocker():
    s, client = session();s.read()
    client.read_partitions.return_value = fixture()['partitions'] + [{'id': '2', 'is-fault': True}]
    with pytest.raises(ProbeError, match='guasto centrale'):
        s.command('1', 'fullset')
    client.set_partition.assert_not_called()


@pytest.mark.parametrize('code', ['installer', 'appAccessDisabled'])
def test_panel_access_denied_recovers_without_login(code):
    s, client = session();s.read()
    def denied(token):
        client.last_error = {'http_status': 403, 'server_code': code, 'operation': 'async/system/'}
        raise PanelUnavailableError('Accesso sospeso')
    client.read_panel.side_effect = denied
    for _ in range(3):
        with pytest.raises(PanelUnavailableError):s.read()
    client.login.assert_called_once()
    client.clear.assert_not_called()
    client.read_panel.side_effect = None
    client.last_error = None
    assert s.read()['partitions']['1']['state'] == 'unset'
    client.login.assert_called_once()
    client.set_partition.assert_not_called()


def test_expired_session_renews_once_and_recovers():
    s, client = session();s.read()
    def expired(token):
        client.last_error = {'http_status': 401, 'operation': 'async/system/'}
        client.read_panel.side_effect = None
        raise ProbeError('expired')
    client.read_panel.side_effect = expired
    assert s.read()['partitions']['1']['state'] == 'unset'
    assert client.login.call_count == 2
    client.set_partition.assert_not_called()


def test_repeated_read_rejection_backs_off_then_recovers():
    s, client = session()
    def denied(token):
        client.last_error = {'http_status': 403, 'operation': 'async/system/'}
        raise ProbeError('denied')
    client.read_panel.side_effect = denied
    with patch('custom_components.eaton_secureconnect.session.monotonic') as clock:
        clock.return_value = 0
        with pytest.raises(ProbeError) as error:s.read()
        assert not isinstance(error.value, AuthError)
        assert client.login.call_count == 2
        clock.return_value = 59
        with pytest.raises(ProbeError):s.read()
        assert client.login.call_count == 2
        clock.return_value = 60
        with pytest.raises(ProbeError):s.read()
        assert client.login.call_count == 4
        clock.return_value = 179
        with pytest.raises(ProbeError):s.read()
        assert client.login.call_count == 4
        client.read_panel.side_effect = None
        client.last_error = None
        clock.return_value = 180
        assert s.read()['partitions']['1']['state'] == 'unset'
        assert client.login.call_count == 5
    client.set_partition.assert_not_called()


@pytest.mark.parametrize('diagnostic', [
    {'http_status': 401, 'operation': 'login'},
    {'http_status': 400, 'operation': 'login', 'server_code': 'loginPasswordPairIsNotExists'},
    {'http_status': 403, 'operation': 'login', 'server_code': 'confirmation.needed'},
])
def test_confirmed_bad_credentials_still_require_reauth(diagnostic):
    s, client = session()
    def rejected(*args):
        client.last_error = diagnostic
        raise ProbeError('rejected')
    client.login.side_effect = rejected
    with pytest.raises(AuthError):s.read()
    client.login.assert_called_once()
    client.set_partition.assert_not_called()


@pytest.mark.parametrize('status', [401, 403])
def test_auth_failure_during_write_never_replays_command(status):
    s, client = session();s.read()
    def rejected(*args):
        client.last_error = {'http_status': status, 'operation': 'async/system/partitions-1/'}
        raise ProbeError('rejected')
    client.set_partition.side_effect = rejected
    with pytest.raises(ProbeError, match='Esito incerto'):s.command('1', 'fullset')
    client.set_partition.assert_called_once()
    client.login.assert_called_once()
