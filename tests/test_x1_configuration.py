"""Cold HA imports and private grant persistence in an isolated process."""

import subprocess
import sys
import unittest
from pathlib import Path


class X1ConfigurationTests(unittest.TestCase):
    def test_real_ha_options_storage_and_setup_fail_closed(self):
        # Other suites bypass package initializers to test individual modules.
        # This process deliberately exercises the real cold import instead.
        code = r"""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import tempfile
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import frame
from homeassistant.helpers.storage import Store

import custom_components.xtool as integration
from custom_components.xtool.config_flow import XtoolOptionsFlow
from custom_components.xtool.protocols.ws_v2.x1_auth import (
    X1AuthError, X1Grant, grant_store, load_grant,
)

HOST = '192.0.2.12'
SERIAL = 'SYNTHETIC-X1-NOT-REAL'

def entry(options=None):
    return ConfigEntry(
        data={'host':HOST, 'serial_number':SERIAL, 'device_name':'Synthetic X1', 'model_key':'JS001_V2'},
        options=options or {}, domain='xtool', source='user', title='Synthetic X1',
        unique_id=SERIAL, version=1, minor_version=1,
        discovery_keys=MappingProxyType({}), subentries_data=[],
    )

def flow(hass, config_entry):
    result = XtoolOptionsFlow(config_entry)
    result.hass = hass
    return result

async def main():
    with tempfile.TemporaryDirectory() as directory:
        hass = HomeAssistant(directory)
        frame.async_setup(hass)
        config_entry = entry()
        grant = X1Grant.parse({
            'host':HOST, 'serial':SERIAL,
            'client_id':'00000000-0000-4000-8000-000000000001',
            'consumer':'synthetic-test-consumer', 'leaf_sha256':'00' * 32,
            'user_id':'mk-guest', 'timezone':'UTC', 'token':secrets.token_hex(24),
        }, host=HOST, serial=SERIAL)
        options = flow(hass, config_entry)
        menu = await options.async_step_init()
        assert menu['menu_options'] == ['x1_grant', 'x1_options']
        form = await options.async_step_x1_grant()
        assert form['data_schema']({'grant':'value'}) == {'grant':'value'}
        imported = await options.async_step_x1_grant({'grant':json.dumps(asdict(grant))})
        assert imported['data'] == {'x1_grant_configured': True}
        assert grant.token not in repr(imported)
        assert dict(config_entry.options) == {}
        stored = await load_grant(hass, config_entry.entry_id, host=HOST, serial=SERIAL)
        assert stored == grant
        path = Path(grant_store(hass, config_entry.entry_id).path)
        assert path.stat().st_mode & 0o777 == 0o600
        try:
            await load_grant(hass, config_entry.entry_id, host='192.0.2.13', serial=SERIAL)
        except X1AuthError:
            pass
        else:
            raise AssertionError('Host rebinding was accepted')
        configured = entry({'x1_grant_configured':True})
        preserved = await flow(hass, configured).async_step_x1_options({'scan_interval':120})
        assert preserved['data'] == {'x1_grant_configured':True, 'scan_interval':120}
        for raw in ('{}', 'x' * 16385):
            result = await flow(hass, config_entry).async_step_x1_grant({'grant':raw})
            assert result['errors'] == {'base':'invalid_x1_grant'}
            assert 'data' not in result
        invalid = asdict(grant)
        invalid['client_id'] = invalid['client_id'].replace('-', '')
        result = await flow(hass, config_entry).async_step_x1_grant({'grant':json.dumps(invalid)})
        assert result['errors'] == {'base':'invalid_x1_grant'}
        invalid = asdict(grant)
        invalid['token'] = 'x' * 8193
        result = await flow(hass, config_entry).async_step_x1_grant({'grant':json.dumps(invalid)})
        assert result['errors'] == {'base':'invalid_x1_grant'}
        fresh = entry()
        with patch.object(Store, 'async_save', new=AsyncMock()):
            failed = await flow(hass, fresh).async_step_x1_grant({'grant':json.dumps(asdict(grant))})
        assert failed['errors'] == {'base':'x1_grant_storage_failed'}
        assert 'data' not in failed
        assert grant.token not in repr(failed)
        # A configured but missing grant must never fall back to discovery.
        with patch('custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2', new=AsyncMock()) as probe:
            try:
                await integration.async_setup_entry(hass, configured)
            except ConfigEntryAuthFailed as error:
                assert grant.token not in str(error)
                assert SERIAL not in str(error)
                assert error.__suppress_context__
            else:
                raise AssertionError('Missing configured grant did not fail closed')
            probe.assert_not_awaited()
        await hass.async_block_till_done()
        print('Cold HA import, private persistence, masked options and fail-closed setup verified')

asyncio.run(main())
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("private persistence", result.stdout)
