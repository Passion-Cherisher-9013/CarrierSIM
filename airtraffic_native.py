"""Linux com.apple.atc host, isolated from CarrierSIM's backup and recovery code.

Wire format and handshake follow the MIT-licensed AirCard-Linux implementations.
No device connection is made by self_check().
"""
from __future__ import annotations

import asyncio
import contextlib
import plistlib
import sys
import uuid
from collections import Counter

MAX_ATC_FRAME = 16 * 1024 * 1024
BACKEND = 'linux-native-atc'
# Published AirCard-Linux compatibility token for Grappa v1/device 0/protocol 1.
GRAPPA_TOKEN = bytes.fromhex(
    '01012ba6a01f2ccf66a02613d5b72e0bc916004058a001a6874d18b5bd7b3395e25d79f'
    'a3ffcc67e718106d485c51540b828d1620e9f94f582d3bcc6f97e9088c923095ad8d36'
    'ab568fb45df61e286d25354b04c'
)


class AtcError(RuntimeError):
    pass


def frame(message):
    raw = plistlib.dumps(message, fmt=plistlib.FMT_BINARY, sort_keys=False)
    if len(raw) > MAX_ATC_FRAME:
        raise AtcError('AirTraffic message is too large')
    return len(raw).to_bytes(4, 'little') + raw


async def self_check():
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.service_connection import ServiceConnection

    if not callable(create_using_usbmux) or not all(
        callable(getattr(ServiceConnection, name, None))
        for name in ('recvall', 'sendall', 'close')
    ):
        raise AtcError('Incompatible pymobiledevice3 version for AirTraffic')
    sample = {'Command': 'Ping', 'Session': 1}
    encoded = frame(sample)
    if int.from_bytes(encoded[:4], 'little') != len(encoded) - 4 or plistlib.loads(encoded[4:]) != sample:
        raise AtcError('AirTraffic encoding failed')
    if len(GRAPPA_TOKEN) != 84:
        raise AtcError('Invalid Grappa token')
    return {'ok': True, 'backend': BACKEND, 'deviceConnections': 0}


class NativeAtcClient:
    def __init__(self, service, emit):
        self.service = service
        self.emit = emit
        self.messages = asyncio.Queue()
        self.reader = None
        self.grappa_support = None
        self.grappa_sent = False

    async def read_message(self):
        header = await self.service.recvall(4)
        if len(header) != 4:
            raise AtcError('AirTraffic connection closed')
        length = int.from_bytes(header, 'little')
        if not 0 < length <= MAX_ATC_FRAME:
            raise AtcError(f'Invalid AirTraffic frame length: {length}')
        raw = await self.service.recvall(length)
        if len(raw) != length:
            raise AtcError('Incomplete AirTraffic message')
        try:
            message = plistlib.loads(raw)
        except Exception as error:
            raise AtcError('Invalid AirTraffic binary plist') from error
        if not isinstance(message, dict):
            raise AtcError('AirTraffic message is not a dictionary')
        command, session = message.get('Command'), message.get('Session')
        if not isinstance(command, str) or type(session) is not int or session not in (0, 1):
            raise AtcError('Invalid AirTraffic envelope')
        if message.get('Type') != 0:
            raise AtcError('Unsupported AirTraffic message type')
        params = message.get('Params', {})
        if not isinstance(params, dict):
            raise AtcError('Invalid AirTraffic parameters')
        return message

    async def send_message(self, command, session, params=None):
        message = {'Command': command, 'Session': session}
        if params is not None:
            message['Params'] = params
        await self.service.sendall(frame(message))

    async def reader_loop(self):
        try:
            while True:
                message = await self.read_message()
                command = message['Command']
                if command == 'Ping':
                    await self.send_message('Pong', 1)
                    continue
                if command == 'Capabilities':
                    support = message.get('Params', {}).get('GrappaSupportInfo')
                    if support is not None:
                        self.grappa_support = support
                await self.messages.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.messages.put(error)

    async def next_message(self):
        message = await self.messages.get()
        if isinstance(message, Exception):
            raise message
        command = message['Command']
        self.emit({'event': 'message', 'name': command, 'session': message['Session']})
        if command == 'SyncFailed':
            code = message.get('Params', {}).get('ErrorCode')
            raise AtcError(f'iPhone rejected AirTraffic sync (code {code})')
        return message

    async def wait_for(self, wanted, session):
        while True:
            message = await self.next_message()
            command = message['Command']
            if command == wanted:
                if message['Session'] != session and not (wanted == 'SyncAllowed' and message['Session'] in (0, 1)):
                    raise AtcError(f'Invalid {command} session: {message["Session"]}')
                return message
            if command == 'SyncFinished':
                raise AtcError('Synchronization ended prematurely')
            if command == 'SyncAllowed' and wanted != 'SyncAllowed' and message['Session'] == 1:
                protected = message.get('Params', {}).get('DataProtected')
                if protected is True:
                    raise AtcError('iPhone is locked or its data is protected')
                if protected is False:
                    continue
            if command in ('Capabilities', 'InstalledAssets', 'AssetMetrics'):
                if command == 'Capabilities' and self.grappa_support is not None and not self.grappa_sent and wanted != 'SyncAllowed':
                    raise AtcError('Grappa capability arrived after HostInfo; synchronization stopped')
                continue
            raise AtcError(f'Unexpected AirTraffic message: {command}')

    async def handshake(self):
        allowed = await self.wait_for('SyncAllowed', 0)
        protected = allowed.get('Params', {}).get('DataProtected')
        if protected is True:
            raise AtcError('iPhone is locked or its data is protected')
        if protected is not None and protected is not False:
            raise AtcError('Invalid DataProtected field')
        # Capabilities may trail SyncAllowed in the startup burst.
        await asyncio.sleep(.1)
        while not self.messages.empty():
            queued = await self.next_message()
            if queued['Command'] != 'Capabilities':
                raise AtcError(f'Unexpected message before HostInfo: {queued["Command"]}')
        support = self.grappa_support
        if support is not None:
            keys = ('version', 'deviceType', 'protocolVersion')
            if (not isinstance(support, dict) or
                    any(type(support.get(k)) is not int for k in keys) or
                    tuple(support[k] for k in keys) != (1, 0, 1)):
                raise AtcError(f'AirTraffic uses an unsupported Grappa variant: {support}')
        host_info = {
            'Type': 'iTunes', 'Version': '13.7.0.161', 'MacOSVersion': 'Linux',
            'SyncHostName': 'CarrierSIM', 'LibraryID': str(uuid.uuid4()),
            'SyncedDataclasses': ['Book'], 'SyncedAssetTypes': ['Book'], 'Wakeable': False,
        }
        if support is not None:
            host_info['Grappa'] = GRAPPA_TOKEN
            self.grappa_sent = True
        await self.send_message('HostInfo', 0, {'HostInfo': host_info, 'LocalCloudSupport': False})
        await asyncio.sleep(.2)
        request = {'Dataclasses': ['Book'], 'DataclassAnchors': {}, 'HostInfo': host_info}
        if support is not None:
            request['Grappa'] = GRAPPA_TOKEN
        await self.send_message('RequestingSync', 1, request)
        await self.wait_for('ReadyForSync', 1)

    async def wait_for_commit(self, authorize):
        approval = asyncio.create_task(authorize())
        incoming = asyncio.create_task(self.next_message())
        try:
            while True:
                done, _ = await asyncio.wait((approval, incoming), return_when=asyncio.FIRST_COMPLETED)
                if incoming in done:
                    message = incoming.result()
                    command = message['Command']
                    if command not in ('AssetMetrics', 'InstalledAssets', 'Capabilities'):
                        raise AtcError(f'Unexpected message before commit: {command}')
                    if command == 'Capabilities' and self.grappa_support is not None and not self.grappa_sent:
                        raise AtcError('Grappa capability arrived after HostInfo; synchronization stopped')
                    incoming = asyncio.create_task(self.next_message())
                    continue
                if approval in done:
                    if not approval.result():
                        raise AtcError('Backup was not confirmed')
                    return
        finally:
            for task in (approval, incoming):
                if not task.done():
                    task.cancel()
            await asyncio.gather(approval, incoming, return_exceptions=True)

    async def sync_assets(self, assets, authorize):
        await self.send_message('FinishedSyncingMetadata', 1,
            {'SyncTypes': {'Book': 1}, 'DataclassAnchors': {}})
        message = await self.wait_for('AssetManifest', 1)
        manifest = message.get('Params', {}).get('AssetManifest')
        if not isinstance(manifest, dict) or not isinstance(manifest.get('Book'), list):
            raise AtcError('Invalid AirTraffic manifest')
        books = manifest['Book']
        expected = [asset_id for asset_id, _ in assets]
        if len(expected) != len(set(expected)):
            raise AtcError('Duplicate expected AssetIDs')
        if any(isinstance(row, dict) and not isinstance(row.get('AssetID'), str) for row in books):
            raise AtcError('Invalid AssetID in AirTraffic manifest')
        counts = Counter(row.get('AssetID') for row in books if isinstance(row, dict))
        downloadable = {row.get('AssetID') for row in books
                        if isinstance(row, dict) and row.get('IsDownload') is True}
        missing = [asset_id for asset_id in expected if counts[asset_id] == 0 or asset_id not in downloadable]
        duplicate = [asset_id for asset_id in expected if counts[asset_id] > 1]
        if missing or duplicate:
            self.emit({'event': 'manifest', 'expected': len(assets), 'matched': len(assets) - len(missing),
                       'duplicate': len(duplicate)})
            raise AtcError(f'AirTraffic did not confirm the required assets: {len(missing)} missing, {len(duplicate)} duplicated')
        self.emit({'event': 'manifest', 'expected': len(assets), 'matched': len(assets)})
        for index, (asset_id, destination) in enumerate(assets):
            if index == 2:
                self.emit({'event': 'before-final-asset'})
                await self.wait_for_commit(authorize)
            await self.send_message('FileComplete', 1,
                {'AssetID': asset_id, 'Dataclass': 'Book', 'AssetPath': destination})
            if index + 1 < len(assets):
                await asyncio.sleep(.9)
        await self.wait_for('SyncFinished', 1)
        self.emit({'event': 'sync-finished'})


async def run_session(udid, connection_type, assets, emit, authorize, *, probe=False, connector=None):
    if connector is None:
        from pymobiledevice3.lockdown import create_using_usbmux
        connector = create_using_usbmux
    device = await asyncio.wait_for(connector(serial=udid, autopair=False, connection_type=connection_type), 15)
    try:
        service = await device.start_lockdown_service('com.apple.atc')
        try:
            client = NativeAtcClient(service, emit)
            client.reader = asyncio.create_task(client.reader_loop())
            try:
                try:
                    async with asyncio.timeout(150):
                        await client.handshake()
                        if not probe:
                            await client.sync_assets(assets, authorize)
                except TimeoutError as error:
                    raise AtcError('AirTraffic timed out') from error
            finally:
                client.reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await client.reader
        finally:
            await service.close()
    finally:
        await device.close()
    emit({'ok': True, 'backend': BACKEND, 'probe': probe})


async def run_worker(udid, assets, connection_type, emit, probe=False):
    if udid is None:
        emit(await self_check())
        return
    emit({'event': 'backend', 'name': BACKEND})

    async def authorize():
        return (await asyncio.to_thread(sys.stdin.readline)).strip() == 'CONTINUE'

    await run_session(udid, connection_type, assets, emit, authorize, probe=probe)
