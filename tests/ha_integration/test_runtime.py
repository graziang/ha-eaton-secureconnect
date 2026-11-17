from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch, Mock
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries, ConfigEntryState
from homeassistant.loader import async_setup as setup_loader
from homeassistant.helpers.frame import async_setup as setup_frame
from homeassistant.helpers import device_registry as dr, entity_registry as er
from custom_components.eaton_secureconnect.model import normalize
from custom_components.eaton_secureconnect.api import ProbeError
from test_model_session import fixture

@pytest.mark.asyncio
async def test_real_ha_discovery_events_unavailable_unload(tmp_path):
    (tmp_path/'custom_components').symlink_to(Path('custom_components').resolve(), target_is_directory=True)
    hass=HomeAssistant(str(tmp_path))
    setup_loader(hass)
    setup_frame(hass)
    hass.config_entries=ConfigEntries(hass,{})
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass);await er.async_load(hass)
    entry=ConfigEntry(domain='eaton_secureconnect',title='Eaton',data={},options={},source='user',version=1,minor_version=1,unique_id='test_panel',discovery_keys=MappingProxyType({}),subentries_data=[])
    fake=Mock();fake.read.side_effect=lambda:normalize(fixture())
    events=[]
    hass.bus.async_listen('eaton_secureconnect_event',lambda e:events.append(e.data))
    try:
        with patch('custom_components.eaton_secureconnect.Session',return_value=fake):
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
        assert entry.state==ConfigEntryState.LOADED
        states=hass.states.async_all()
        assert len(states)==4,[(s.entity_id,s.state) for s in states]
        assert len(dr.async_get(hass).devices)==2
        assert not events
        c=entry.runtime_data
        raw=fixture();raw['logs']=[{'id':'1','desc':'test'}]
        raw['zones'].append({'id':'2','name':'Second window','state':'open','omitted':False})
        fake.read.side_effect=lambda:normalize(raw)
        await c.async_refresh();await hass.async_block_till_done()
        assert len(hass.states.async_all())==5
        assert len(dr.async_get(hass).devices)==3
        assert len(events)==1
        await c.async_refresh();await hass.async_block_till_done()
        assert len(events)==1
        fake.read.side_effect=ProbeError('network')
        await c.async_refresh();await hass.async_block_till_done()
        assert all(s.state=='unavailable' for s in hass.states.async_all())
        assert await hass.config_entries.async_unload(entry.entry_id)
        fake.close.assert_called_once()
    finally:
        await hass.async_stop(force=True)
