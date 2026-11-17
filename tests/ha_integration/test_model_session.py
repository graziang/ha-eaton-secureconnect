import copy
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from custom_components.eaton_secureconnect.api import ProbeError
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
    with pytest.raises(AuthError):s.read()
    assert client.login.call_count==2
    client.set_partition.assert_not_called()


def test_network_failure_does_not_relogin():
    s,client=session();s.read()
    client.read_panel.side_effect=ProbeError('network')
    with pytest.raises(ProbeError):s.read()
    assert client.login.call_count==1
