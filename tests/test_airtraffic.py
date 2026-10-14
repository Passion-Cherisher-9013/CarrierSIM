"""AirTraffic protocol regressions; no Apple libraries or device required."""
import io
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import carrier
import airtraffic_apple as apple


class FakeAppleHost:
    def __init__(self, platform, assets=(), reject=False, missing=False, connect=True, send_ok=True):
        self.send_ok = send_ok
        self.platform = platform
        self.assets = assets
        self.reject = reject
        self.missing = missing
        self.connection = object() if connect else None
        self.events = []
        self.completed = []
        self.released = []
        self.closed = False
        self.host_info = None
        self.library = None
        self.messages = [{'name': name} for name in ('InstalledAssets', 'AssetMetrics', 'SyncAllowed')]
        self.cf = SimpleNamespace(CFRelease=self.released.append)
        self.at = SimpleNamespace(
            ATHostConnectionCreate=self.create,
            ATHostConnectionCreateWithLibrary=self.create_with_library,
            ATHostConnectionGetCurrentSessionNumber=lambda connection: 7,
            ATCFMessageCreate=self.message,
            ATHostConnectionSendMessage=self.send_message,
            ATHostConnectionReadMessage=self.read_message,
            ATCFMessageGetName=lambda message: message['name'],
            ATCFMessageGetParam=lambda message, key: message['params'][key],
            ATHostConnectionRelease=lambda connection: self.events.append(('release', connection)))

    def encode(self, value): return value
    def decode(self, value): return value
    def close(self): self.closed = True

    def create(self, udid):
        self.events.append(('create', udid))
        return self.connection

    def create_with_library(self, library, udid, flags):
        self.library = library
        self.events.append(('create-library', library, udid, flags))
        return self.connection

    def read_message(self, connection):
        # Windows must receive HostInfo before it will allow the sync.
        if self.platform == 'win32' and self.host_info is None:
            return {'name': 'SyncFailed'}
        message = self.messages.pop(0)
        self.events.append(('read', message['name']))
        return message

    def message(self, session, command, params):
        return {'session': session, 'name': command, 'params': params}

    def send_message(self, connection, message):
        self.events.append(('request', message))
        if self.send_ok:
            self.messages += [{'name': name} for name in
                              ('SyncAllowed', 'SyncFailed' if self.reject else 'ReadyForSync')]
        return self.send_ok

    def call(self, name, connection, *values):
        self.events.append((name, *values))
        if name == 'ATHostConnectionSendHostInfo':
            self.host_info = values[0]
        elif name == 'ATHostConnectionSendSyncRequest':
            # Model the failing Windows DLL path: the new exchange must avoid it.
            if self.platform == 'win32':
                raise RuntimeError('invalid Grappa from Windows SendSyncRequest')
            self.messages.append({'name': 'ReadyForSync'})
        elif name == 'ATHostConnectionSendMetadataSyncFinished':
            books = [{'AssetID': asset, 'IsDownload': True} for asset, _ in self.assets]
            if self.missing: books = books[:-1]
            self.messages.append({'name': 'AssetManifest', 'params': {'AssetManifest': {'Book': books}}})
        elif name == 'ATHostConnectionSendAssetCompleted':
            self.completed.append(values)


class AirTrafficTest(unittest.TestCase):
    ASSETS = [('source', 'first'), ('link', 'second'), ('saved', 'final')]

    def run_host(self, host, udid='phone', stdin=None):
        with patch.object(apple, 'AppleHost', return_value=host), \
                patch.object(carrier.sys, 'platform', host.platform), \
                patch.object(carrier.sys, 'stdin', stdin or io.StringIO('CONTINUE\n')), \
                patch.object(apple.time, 'sleep'), patch('platform.mac_ver', return_value=('15.0', (), 'arm64')), \
                patch.object(carrier, 'framed') as frames:
            carrier.host_worker({'udid': udid, 'assets': host.assets, 'directories': []})
        return [call.args[0] for call in frames.call_args_list]

    def assert_closed(self, host):
        self.assertTrue(host.closed)
        self.assertEqual(sum(event[0] == 'release' for event in host.events), 1)

    def test_windows_probe_uses_one_consistent_library_and_reaches_ready_without_assets(self):
        host = FakeAppleHost('win32')
        frames = self.run_host(host)
        self.assertEqual(host.events[0], ('create-library', host.library, 'phone', 0))
        self.assertEqual(host.host_info['LibraryID'], host.library)
        request = next(event[1] for event in host.events if event[0] == 'request')
        self.assertEqual((request['session'], request['name']), (7, 'RequestingSync'))
        self.assertEqual(request['params']['Dataclasses'], ['Book'])
        self.assertEqual(request['params']['DataclassAnchors'], {'Book': '0'})
        self.assertEqual(request['params']['HostInfo']['LibraryID'], host.library)
        self.assertIsInstance(request['params']['HostInfo']['Grappa'], bytes)
        self.assertEqual(len(request['params']['HostInfo']['Grappa']), 84)
        self.assertFalse(host.completed)
        self.assertFalse(any('MetadataSyncFinished' in event[0] for event in host.events))
        self.assertEqual(frames[-1], {'ok': True, 'probe': True})
        self.assert_closed(host)

    def test_windows_transfer_preserves_the_backup_acknowledgement_before_final_asset(self):
        host = FakeAppleHost('win32', self.ASSETS)
        def acknowledge():
            self.assertEqual(len(host.completed), 2)
            return 'CONTINUE\n'
        frames = self.run_host(host, stdin=SimpleNamespace(readline=acknowledge))
        self.assertEqual(host.completed, [(asset, 'Book', destination) for asset, destination in self.ASSETS])
        metadata = next(event for event in host.events if 'MetadataSyncFinished' in event[0])
        self.assertEqual(metadata[1:], ({'Book': 1}, {'Book': '0'}))
        power = next(event for event in host.events if 'PowerAssertion' in event[0])
        self.assertEqual(power[1:], (True,))
        self.assertLess(host.events.index(power), host.events.index(metadata))
        self.assertIn({'event': 'before-final-asset'}, frames)
        self.assertEqual(frames[-1], {'ok': True})
        self.assert_closed(host)

    def test_unconfirmed_backup_prevents_the_final_asset_and_closes_the_connection(self):
        host = FakeAppleHost('win32', self.ASSETS)
        with self.assertRaises(RuntimeError):
            self.run_host(host, stdin=io.StringIO('STOP\n'))
        self.assertEqual(len(host.completed), 2)
        self.assert_closed(host)

    def test_refused_handshake_never_syncs_metadata_or_completes_an_asset(self):
        host = FakeAppleHost('win32', self.ASSETS, reject=True)
        with self.assertRaises(RuntimeError):
            self.run_host(host)
        self.assertFalse(host.completed)
        self.assertFalse(any('MetadataSyncFinished' in event[0] for event in host.events))
        self.assert_closed(host)

    def test_failed_send_reports_the_send_error_without_waiting_for_ready(self):
        host = FakeAppleHost('win32', self.ASSETS, send_ok=False)
        with self.assertRaisesRegex(RuntimeError, r'ATHostConnectionSendMessage\(RequestingSync\)'):
            self.run_host(host)
        self.assertEqual([event[1] for event in host.events if event[0] == 'read'],
                         ['InstalledAssets', 'AssetMetrics', 'SyncAllowed'])
        request = next(event[1] for event in host.events if event[0] == 'request')
        self.assertIn(request, host.released)
        self.assertFalse(host.completed)
        self.assertFalse(any('MetadataSyncFinished' in event[0] for event in host.events))
        self.assert_closed(host)

    def test_apple_module_import_does_not_load_dlls_or_import_carrier(self):
        code = """
import ctypes
import sys
def unexpected_load(*args, **kwargs):
    raise AssertionError('Library loaded during import')
ctypes.CDLL = unexpected_load
import airtraffic_apple
assert 'carrier' not in sys.modules
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=carrier.ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_linux_dispatch_does_not_enter_the_apple_transport(self):
        from unittest.mock import AsyncMock
        assets = [('source', 'destination')]
        real_platform = sys.platform
        async def run_native(*args):
            self.assertEqual(sys.platform, real_platform)
        # asyncio initializes a global platform-specific policy on its first run.
        # Select the backend without changing the host OS seen by asyncio.
        with patch.object(carrier, 'host_backend', return_value='linux-native-atc'), \
                patch.object(apple, 'run_worker') as worker, \
                patch('airtraffic_native.run_worker', new_callable=AsyncMock, side_effect=run_native) as native:
            carrier.host_worker({'udid': 'phone', 'assets': assets, 'connection': 'Network', 'probe': True})
        worker.assert_not_called()
        native.assert_awaited_once_with('phone', assets, 'Network', carrier.framed, True)

    def test_missing_manifest_asset_prevents_all_asset_completions(self):
        host = FakeAppleHost('win32', self.ASSETS, missing=True)
        with self.assertRaises(RuntimeError):
            self.run_host(host)
        self.assertFalse(host.completed)
        self.assert_closed(host)

    def test_macos_keeps_the_existing_connection_request_and_metadata_anchors(self):
        host = FakeAppleHost('darwin', self.ASSETS)
        self.run_host(host)
        self.assertEqual(host.events[0], ('create', 'phone'))
        self.assertFalse(any(event[0] == 'request' or 'PowerAssertion' in event[0] for event in host.events))
        legacy = next(event for event in host.events if 'SendSyncRequest' in event[0])
        self.assertEqual(legacy[1:3], (['Book'], {}))
        self.assertEqual(host.host_info['Type'], 'iTunes')
        self.assertIn('MacOSVersion', host.host_info)
        metadata = next(event for event in host.events if 'MetadataSyncFinished' in event[0])
        self.assertEqual(metadata[1:], ({'Book': 1}, {}))
        self.assertEqual(len(host.completed), 3)
        self.assert_closed(host)

    def test_library_check_does_not_connect_to_a_device(self):
        host = FakeAppleHost('win32')
        frames = self.run_host(host, udid=None)
        self.assertFalse(host.events)
        self.assertTrue(host.closed)
        self.assertEqual(frames[-1], {'ok': True, 'deviceConnections': 0})

    def test_failed_connection_closes_the_host_without_releasing_null(self):
        host = FakeAppleHost('win32', connect=False)
        with self.assertRaises(RuntimeError):
            self.run_host(host)
        self.assertTrue(host.closed)
        self.assertFalse(any(event[0] == 'release' for event in host.events))
