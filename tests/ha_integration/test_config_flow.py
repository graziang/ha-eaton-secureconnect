from unittest.mock import Mock, patch
from types import MappingProxyType
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntries
from homeassistant.loader import async_setup
from homeassistant.helpers.frame import async_setup as frame_setup
from custom_components.eaton_secureconnect.config_flow import EatonConfigFlow, EatonOptionsFlow

@pytest.mark.asyncio
async def test_config_flow_validation_and_creation(tmp_path):
    hass=HomeAssistant(str(tmp_path));async_setup(hass);frame_setup(hass)
    hass.config_entries=ConfigEntries(hass,{})
    await hass.config_entries.async_initialize()
    flow=EatonConfigFlow();flow.hass=hass;flow.context={'source':'user'}
    try:
        result=await flow.async_step_user()
        assert result['step_id']=='user'
        data={'username':'fake','password':'fake','application_id':'not-uuid','pairing_name':'Test'}
        assert (await flow.async_step_user(data))['errors']=={'application_id':'invalid_identity'}
        data['application_id']='00000000-0000-4000-8000-000000000001'
        with patch('custom_components.eaton_secureconnect.config_flow.validate') as validate:
            result=await flow.async_step_user(data)
        assert result['type']=='create_entry'
        validate.assert_called_once()
        assert result['data']==data
    finally:
        await hass.async_stop(force=True)
