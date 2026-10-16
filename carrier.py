#!/usr/bin/env python3
"""Carrier profiles using exact IMSI aliases and explicit experimental GID1 aliases."""
from __future__ import annotations
import argparse
import asyncio
import contextlib
import functools
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import signal
import stat
import struct
import sys
import tempfile
import threading
import time
import zipfile
from carriersim_version import VERSION
from device_models import MODELS

SELF = Path(__file__).resolve()
ROOT = SELF.parent

PARENT = '/var/mobile/Library/Carrier Bundles'
DEVICE_FAMILY = 'iPhone'
# IMSI/ICCID of the phone's SIMs, filled by carrier_rows: masked exactly in every log line.
LOG_SECRETS = {}
TARGET = PARENT + '/' + DEVICE_FAMILY
SYSTEM_BUNDLE_DIR = 'System/Library/Carrier Bundles/' + DEVICE_FAMILY
PAYLOAD_PATH = 'q0/q1/q2/q3/q4/payload'
BUNDLE = 'Vodafone_hu.bundle'
MAX_BYTES = 64 * 1024 * 1024
MAX_NODES = 4000
BOOK_FILES = ('Books/Books.plist', 'Books/Sync/Books.plist', 'Books/Sync/Upload.plist',
              'Books/Sync/Database/OutstandingAssets_4.sqlite',
              'Books/Sync/Database/OutstandingAssets_4.sqlite-shm',
              'Books/Sync/Database/OutstandingAssets_4.sqlite-wal')
BOOK_DIRS = ('Books', 'Books/Managed', 'Books/Sync', 'Books/Sync/Database')
AFC_PERM_DENIED = 10  # pymobiledevice3 AfcError.PERM_DENIED
# usbmux transport: 'USB' or 'Network' (Wi-Fi sync, set by --wifi).
CONNECTION = 'USB'
# Over the cable usbmuxd drops the socket as soon as the phone is gone; over Wi-Fi a sleeping
# phone can leave AFC waiting for hours. A Wi-Fi transfer takes about 25 s, host_session caps
# AirTraffic at 170 s. Recovery starts over a fresh connection and restores Books from books.zip.
NETWORK_TRANSFER_SECONDS = 300
NETWORK_BOOKS_SECONDS = 90
# The native AirTraffic host must finish one stage (both sync sessions and the pause) in this time.
AIRTRAFFIC_SECONDS = 170


def network_timeout(seconds):
    return asyncio.timeout(seconds) if CONNECTION == 'Network' else contextlib.nullcontext()



def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def recover_hint():
    # The launcher menu runs this script with CARRIERSIM_MENU=1; its users never type flags.
    if os.environ.get('CARRIERSIM_MENU'):
        return 'choose menu item 5 (Recover after failure)'
    return 'run the script with --recover'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def save_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    # The rename must survive a host crash, not only the contents of the temporary file.
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def safe_name(name):
    require(bool(name) and not name.startswith('/') and '\\' not in name and
            all(p not in ('', '.', '..') for p in name.split('/')), 'Unsafe tree path: ' + name)
    return name


def validate_tree(tree):
    require(len(tree) <= MAX_NODES, 'Too many tree nodes')
    require(sum(len(v[1]) for v in tree.values()) <= MAX_BYTES, 'Tree too large')
    for name, (kind, data) in tree.items():
        safe_name(name)
        require(kind in ('d', 'f', 'l'), 'Unknown node type')
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                require(tree.get(str(parent), (None,))[0] == 'd', 'Missing or non-directory parent')
        if kind == 'l':
            require(b'\x00' not in data and len(data) <= 4096, 'Invalid symlink')

def tree_hash(tree):
    return digest(json.dumps({n: [k, digest(b)] for n, (k, b) in sorted(tree.items())},
                             sort_keys=True).encode())

def zi(name, kind, streaming=False):
    mode = {'f': stat.S_IFREG | 0o644, 'd': stat.S_IFDIR | 0o755,
            'l': stat.S_IFLNK | 0o777}[kind]
    z = zipfile.ZipInfo(name + ('/' if kind == 'd' and not name.endswith('/') else ''),
                        (2026, 9, 24, 0, 0, 0))
    z.create_system = 3
    z.external_attr = mode << 16
    if streaming:
        z.extra = struct.pack('<HHH', 0x5A53, 2, mode)
    return z

def write_tree_zip(path, tree):
    validate_tree(tree)
    with zipfile.ZipFile(path, 'x') as z:
        for name, (kind, data) in sorted(tree.items()):
            z.writestr(zi(name, kind), data)

def read_tree_zip(path):
    tree = {}
    with zipfile.ZipFile(path) as z:
        require(len(z.infolist()) <= MAX_NODES and sum(i.file_size for i in z.infolist()) <= MAX_BYTES,
                'Archive too large')
        for i in z.infolist():
            name = safe_name(i.filename.rstrip('/'))
            require(name not in tree, 'Duplicate archive entry')
            mode = stat.S_IFMT(i.external_attr >> 16)
            require(mode in (0, stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK), 'Unsupported archive type')
            tree[name] = ('d' if i.is_dir() else 'l' if mode == stat.S_IFLNK else 'f', z.read(i))
    validate_tree(tree)
    return tree

def bundle_info(tree, name=BUNDLE):
    prefix = name + '/'
    def pl(file):
        value = tree.get(prefix + file)
        require(value is not None and value[0] == 'f', 'Missing ' + prefix + file)
        return plistlib.loads(value[1])
    info, carrier = pl('Info.plist'), pl('carrier.plist')
    require(any(n.startswith(prefix + 'signatures/') and k == 'f' for n, (k, _) in tree.items()),
            'No signature files (presence is not cryptographic verification)')
    return info, carrier

def staging_archive(payload=None):
    # Six staging levels keep system links inside the ZIP while unpacking.
    # After placement the same six '..' components resolve from /private/var/mobile/... to /.
    tree = {'META-INF': ('d', b''), 'META-INF/com.apple.ZipMetadata.plist':
            ('f', plistlib.dumps({'Version': 2}, fmt=plistlib.FMT_BINARY)),
            'p0': ('d', b''), 'p0/p1': ('d', b''), 'p0/p1/p2': ('d', b''),
            'p0/p1/p2/link': ('l', ('../../../' + PARENT[1:]).encode())}
    def directories(path):
        cursor = ''
        for part in path.split('/'):
            cursor += ('/' if cursor else '') + part
            tree[cursor] = ('d', b'')
    directories(PARENT[1:])
    if payload is not None:
        directories(PAYLOAD_PATH)
        system_names = set(TARGET_BUNDLES)
        for kind, data in payload.values():
            if kind == 'l' and data.startswith(SYSTEM_PREFIX.encode()):
                name = data.decode().removeprefix(SYSTEM_PREFIX)
                require(re.fullmatch(r'[A-Za-z0-9_]+\.bundle', name), 'Unexpected system symlink')
                system_names.add(name)
        for name in system_names:
            directories(SYSTEM_BUNDLE_DIR+'/'+name)
        tree.update({PAYLOAD_PATH+'/'+n:v for n,v in payload.items()})
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', allowZip64=False) as z:
        for name, (kind, data) in sorted(tree.items()):
            z.writestr(zi(name, kind, streaming=True), data)
    return b.getvalue()

class _AfcPending(dict):
    # SDK 11.12.5 can fail a response future while its sender is also failing.
    # Observe response errors before the SDK drops ownership; awaiting still raises.
    @staticmethod
    def observe(future):
        if future is not None and future.done() and not future.cancelled():
            future.exception()

    def pop(self, key, *default):
        future = super().pop(key, *default)
        self.observe(future)
        return future

    def clear(self):
        for future in self.values():
            self.observe(future)
        super().clear()


def afc_service(device):
    from pymobiledevice3.services.afc import AfcService
    service = AfcService(device)
    service._afc_pending = _AfcPending()
    return service


async def exists(afc, path):
    from pymobiledevice3.exceptions import AfcFileNotFoundError
    try:
        return await afc.stat(path)
    except AfcFileNotFoundError:
        return None

async def remote_tree(afc, root):
    tree = {}
    total = 0
    async def visit(path, name='', depth=0):
        nonlocal total
        require(depth < 32, 'Directory nesting is too deep: ' + path)
        require(len(tree) < MAX_NODES, f'More than {MAX_NODES} nodes in {root}: safety limit')
        before = await afc.stat(path)
        kind = before['st_ifmt']
        if kind == 'S_IFDIR':
            if name:
                tree[name] = ('d', b'')
            children = sorted(await afc.listdir(path))
            for child in children:
                require(child not in ('', '.', '..') and '/' not in child, 'Invalid remote name')
                await visit(path + '/' + child, name + '/' + child if name else child, depth + 1)
            require(children == sorted(await afc.listdir(path)), 'Remote directory changed')
        elif kind == 'S_IFLNK':
            require(name, 'Root is a symlink')
            tree[name] = ('l', before['LinkTarget'].encode())
        elif kind == 'S_IFREG':
            require(name, 'Root is a file')
            require(before['st_size'] <= MAX_BYTES, f'File exceeds {MAX_BYTES >> 20} MB: {path} ({before["st_size"] >> 20} MB)')
            data = await afc.get_file_contents(path)
            require(len(data) == before['st_size'], 'File changed while reading: ' + path)
            total += len(data)
            require(total <= MAX_BYTES, f'{root} exceeds {MAX_BYTES >> 20} MB (read {total >> 20} MB, '
                                        f'last file {path}, {len(data) >> 10} KB): safety limit')
            tree[name] = ('f', data)
        else:
            raise RuntimeError('Unsupported remote node: ' + path)
        after = await afc.stat(path)
        require(before == after, 'Remote file changed during read: ' + path)
    require((await afc.stat(root))['st_ifmt'] == 'S_IFDIR', 'Carrier root is not a directory')
    await visit(root)
    validate_tree(tree)
    return tree

BOOK_LOCKS = ('Managed/.Managed.plist.lock', 'Sync/.bookSync.lock')


async def read_managed_books(afc):
    # Only what this script touches in Books: AirTraffic's sync files and folders. The user's
    # library (hundreds of MB of books, Purchases, MetadataStore) is never read or copied.
    node = await exists(afc, 'Books')
    if node is None:
        return False, {}
    require(node['st_ifmt'] == 'S_IFDIR', 'Books is not a directory')
    tree = {}
    for path in BOOK_DIRS[1:]:
        found = await exists(afc, path)
        if found is not None:
            require(found['st_ifmt'] == 'S_IFDIR', 'Unexpected Books directory: ' + path)
            tree[path.removeprefix('Books/')] = ('d', b'')
    for path in list(BOOK_FILES) + ['Books/' + rel for rel in BOOK_LOCKS]:
        found = await exists(afc, path)
        if found is None:
            continue
        require(found['st_ifmt'] == 'S_IFREG', 'Unexpected Books sync artifact: ' + path)
        require(found['st_size'] <= MAX_BYTES, f'File exceeds {MAX_BYTES >> 20} MB: {path}')
        data = await afc.get_file_contents(path)
        require(len(data) == found['st_size'], 'File changed while reading: ' + path)
        tree[path.removeprefix('Books/')] = ('f', data)
    validate_tree(tree)
    return True, tree


async def books_snapshot(afc, run):
    # Books may write its sync files in the background: take two identical reads in a row.
    state = await read_managed_books(afc)
    for _ in range(10):
        await asyncio.sleep(1)
        again = await read_managed_books(afc)
        if again == state:
            break
        state = again
    else:
        raise RuntimeError('Books sync files keep changing: close the Books app '
                           'on the iPhone, wait for book downloads to finish and try again.')
    existed, tree = state
    write_tree_zip(run / 'books.zip', tree)
    top = sorted(await afc.listdir('Books')) if existed else []
    save_json(run / 'books.json', {'existed': existed, 'hash': tree_hash(tree), 'top': top})
    return tree, existed


# Book lists where AirTraffic registers synced items. A run whose Books cleanup failed (older
# versions) leaves our fake items there; atc then treats the catalog item as already installed
# (installOnly) and omits it from the manifest, so every later run fails. These entries are ours.
BOOK_LISTS = ('Books/Books.plist', 'Books/Backup-Books.plist', 'Books/Sync/Books.plist')


def our_trace(value):
    # This script's asset IDs: unique airlift-* staging names or the catalog path they resolve to.
    if isinstance(value, bytes):
        return b'airlift-' in value or any(p in value for p in (b'Carrier Bundles/iPhone', b'Carrier Bundles/iPad'))
    value = str(value or '')
    return 'airlift-' in value or value.endswith(('Carrier Bundles/iPhone', 'Carrier Bundles/iPad'))


def ours(item):
    return isinstance(item, dict) and our_trace(item.get('Persistent ID'))


async def purge_stale_books(afc, run):
    from pymobiledevice3.exceptions import AfcException
    removed = {}
    for path in BOOK_LISTS:
        node = await exists(afc, path)
        if node is None or node['st_ifmt'] != 'S_IFREG':
            continue
        raw = await afc.get_file_contents(path)
        try:
            data = plistlib.loads(raw)
        except Exception:
            continue
        items = data.get('Books') if isinstance(data, dict) else None
        if not isinstance(items, list) or not any(ours(i) for i in items):
            continue
        (run / 'books-stale').mkdir(exist_ok=True)
        (run / 'books-stale' / path.replace('/', '_')).write_bytes(raw)
        data['Books'] = [i for i in items if not ours(i)]
        fmt = plistlib.FMT_BINARY if raw.startswith(b'bplist') else plistlib.FMT_XML
        clean = plistlib.dumps(data, fmt=fmt)
        try:
            await afc.set_file_contents(path, clean)
        except AfcException as error:
            # Some Books files are not writable over AFC (status 10, permission denied).
            # An open that failed changed nothing; record it and clean the rest.
            # Any other AFC failure (no space, lost connection) must not pass as "denied".
            if error.status != AFC_PERM_DENIED:
                raise
            removed[path] = 'write denied: ' + error_text(error)
            continue
        require(await afc.get_file_contents(path) == clean, 'Could not clean ' + path)
        removed[path] = len(items) - len(data['Books'])
    return removed



# ---- Leftovers of earlier runs, removed from the phone itself (no need to keep old runs folders).
LEFTOVER = re.compile(r'airlift-(src|link|saved)-[0-9a-f]{20}')
OUTSTANDING_DB = 'Books/Sync/Database/OutstandingAssets_4.sqlite'


async def remove_tree(afc, path):
    # AFC stat does not follow symlinks: a link is removed itself, never what it points to.
    node = await afc.stat(path)
    if node['st_ifmt'] == 'S_IFDIR':
        for child in await afc.listdir(path):
            require(child not in ('', '.', '..') and '/' not in child, 'Invalid remote name')
            await remove_tree(afc, path + '/' + child)
    await afc.rm_single(path)


def read_outstanding(files):
    # OutstandingAssets_4.sqlite with its WAL, as {suffix: bytes}: rows (persistent ID, completed path).
    # Raises sqlite3.Error when the database cannot be read.
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        for suffix, data in files.items(): (Path(d)/('db.sqlite' + suffix)).write_bytes(data)
        db = sqlite3.connect(Path(d)/'db.sqlite')
        try: return db.execute('select ZPERSISTENTID, ZDOWNLOADCOMPLETEPATH from ZBCOUTSTANDINGASSET').fetchall()
        finally: db.close()


async def purge_outstanding(afc, run):
    # Books' queue of unfinished sync downloads. Rows of an interrupted run make atc skip the
    # catalog. Only when every row is ours is the database removed (Books recreates it empty).
    import sqlite3
    files = {}
    for suffix in ('', '-wal', '-shm'):
        if await exists(afc, OUTSTANDING_DB + suffix):
            files[suffix] = await afc.get_file_contents(OUTSTANDING_DB + suffix)
    if '' not in files:
        return None
    try:
        rows = [r[0] or '' for r in read_outstanding(files)]
    except sqlite3.Error as error:
        return 'could not read: ' + error_text(error)
    mine = [x for x in rows if our_trace(x)]
    if not mine:
        return 0
    if len(mine) != len(rows):
        return f'retained: {len(mine)} of {len(rows)} entries belong to this tool'
    (run / 'books-stale').mkdir(exist_ok=True)
    for suffix, data in files.items():
        (run / 'books-stale' / ('OutstandingAssets_4.sqlite' + suffix)).write_bytes(data)
    for suffix in ('-wal', '-shm', ''):
        if suffix in files: await afc.rm_single(OUTSTANDING_DB + suffix)
    return len(mine)


async def clean_phone(device, run):
    report = {}
    async with afc_service(device) as afc:
        for name in sorted(n for n in await afc.listdir('/') if LEFTOVER.fullmatch(n)):
            node = await afc.stat(name)
            if name.startswith('airlift-saved-') and node['st_ifmt'] == 'S_IFDIR':
                # An exported carrier catalog, possibly the only copy of it (a run from another
                # copy of the folder leaves no journal here). Removed only after a local copy
                # is written and read back identical; otherwise it stays on the phone.
                try:
                    tree = await remote_tree(afc, name)
                    (run / 'media-leftovers').mkdir(exist_ok=True)
                    copy = run / 'media-leftovers' / (name + '.zip')
                    write_tree_zip(copy, tree)
                    require(read_tree_zip(copy) == tree, 'backup mismatch')
                except Exception as error:
                    report[name] = 'not removed: local backup was not saved: ' + error_text(error)
                    continue
            try:
                await remove_tree(afc, name)
                report[name] = 'removed'
            except Exception as error:
                report[name] = 'not removed: ' + error_text(error)
        lists = await purge_stale_books(afc, run)
        if lists: report['Books lists'] = lists
        outstanding = await purge_outstanding(afc, run)
        if outstanding: report['Books downloads'] = outstanding
    save_json(run / 'cleanup.json', report)
    DIAG['cleanup'] = report
    return report


async def books_change(afc, path, action, denied):
    # Some Books files are not writable over AFC (status 10) although iOS itself rewrites them.
    # Such a file is left as iOS made it, unless it still holds this script's staging entries.
    from pymobiledevice3.exceptions import AfcException
    try:
        await action
    except AfcException as error:
        if error.status != AFC_PERM_DENIED:
            raise
        data = await afc.get_file_contents(path) if await exists(afc, path) else b''
        require(not our_trace(data),
                'iPhone refuses to modify ' + path + ', which still contains this tool\'s entries. '
                'Backups are saved in runs; do not delete it. '
                'Disconnect the iPhone, close all apps '
                '(swipe them away in the app switcher), then force-restart the iPhone: '
                'quickly press and release Volume Up, then Volume Down, '
                'then hold the side button until the Apple logo appears. '
                'After startup, unlock the iPhone, reconnect it and retry '
                'menu item 5 (--recover) from the same CarrierSIM folder. '
                'If this recurs, report the error and session log to the author.')
        denied.add(path.removeprefix('Books/'))


async def restore_books(afc, tree, existed, top=None):
    denied = set()
    for path in BOOK_FILES:
        rel = path.removeprefix('Books/')
        current = await exists(afc, path)
        require(current is None or current['st_ifmt'] == 'S_IFREG', 'Unexpected Books artifact; keep backup')
        if rel in tree:
            # An unchanged file is not opened for writing at all.
            if current is not None and await afc.get_file_contents(path) == tree[rel][1]:
                continue
            await afc.makedirs(str(PurePosixPath(path).parent))
            await books_change(afc, path, afc.set_file_contents(path, tree[rel][1]), denied)
            if rel not in denied:
                require(await afc.get_file_contents(path) == tree[rel][1], 'Books restore mismatch')
        elif current:
            await books_change(afc, path, afc.rm_single(path), denied)
    # AirTraffic created these empty lock files during the first physical test.
    # Never delete a pre-existing lock or one with unexpected contents/type.
    for rel in BOOK_LOCKS:
        if rel not in tree:
            path = 'Books/' + rel
            node = await exists(afc, path)
            if node is not None:
                require(node['st_ifmt'] == 'S_IFREG' and node['st_size'] == 0,
                        'Unexpected generated Books lock; retain backup')
                await books_change(afc, path, afc.rm_single(path), denied)
    for path in BOOK_DIRS[1:]:
        # A sync folder that existed before (possibly empty) but was removed during the session.
        if path.removeprefix('Books/') in tree and await exists(afc, path) is None:
            await afc.makedirs(path)
    for path in reversed(BOOK_DIRS):
        was_present = existed if path == 'Books' else path.removeprefix('Books/') in tree
        if not was_present and await exists(afc, path) and not await afc.listdir(path):
            await afc.rm_single(path)
    # Only files and folders this function restores must match the backup. The rest of Books
    # (Purchases, MetadataStore, the user's books) may be rewritten by iOS during the session;
    # it cannot be put back from here, so a top-level difference is reported, not a failure.
    _, after = await read_managed_books(afc)
    managed = {p.removeprefix('Books/') for p in BOOK_FILES + BOOK_DIRS[1:]} | set(BOOK_LOCKS)
    tree = {n: v for n, v in tree.items() if n in managed}  # backups from older versions hold all of Books
    # Files iOS does not let us write stay as iOS left them: reported, not a failure.
    tree = {n: v for n, v in tree.items() if n not in denied}
    after = {n: v for n, v in after.items() if n not in denied}
    diff = ([f'+{n}' for n in sorted(after.keys() - tree.keys())] +
            [f'-{n}' for n in sorted(tree.keys() - after.keys())] +
            [f'~{n}' for n in sorted(tree.keys() & after.keys()) if tree[n] != after[n]])
    require(not diff, 'Could not restore the original Books sync files on the iPhone. '
            'Backups are saved in runs; do not delete it. Report this error to the author. '
            'Differences (+ added, - missing, ~ changed): ' + ', '.join(diff[:10]))
    kept = sorted('!' + n + ' (write denied by iOS)' for n in denied)
    if top is None:
        return kept
    now = set(await afc.listdir('Books')) if await exists(afc, 'Books') else set()
    return kept + sorted('+' + n for n in now - set(top)) + sorted('-' + n for n in set(top) - now)


# Device-side view of an AirTraffic session: atc decides which assets enter the manifest.
DEVICE_LOG_KEYS = ('atc', 'airtraffic', 'book', 'sandbox', 'deny', 'airlift', 'carrier bundles',
                   'itunes', 'medialibrary', 'mobile.lockdown')


@contextlib.asynccontextmanager
async def syslog_capture(device, path, keep, status=None, linger=1, *, unified=False):
    # Transfer logs are diagnostic; trigger selection checks require an intact capture.
    # Capture errors are recorded in the file and, when given, status['log_error'].
    # Lines are masked before disk: user bundle links are named by IMSI, and these logs get attached to issues.
    from pymobiledevice3.services.syslog import SyslogService
    from pymobiledevice3.services.os_trace import OsTraceService, OsActivityStreamFlag, OS_TRACE_RELAY_STREAM_FLAGS_DEFAULT
    ready = asyncio.Event()
    async def watch():
        try:
            pid = await commcenter_pid(device, first=True) if unified else None
            require(not unified or pid is not None, 'CommCenter process not found on the iPhone.')
            async with (OsTraceService(device) if unified else SyslogService(device)) as log:
                if not unified:
                    ready.set()
                # Trigger verification must not replay selections from before this rescan.
                rows = (log.syslog(pid=pid, stream_flags=OS_TRACE_RELAY_STREAM_FLAGS_DEFAULT &
                                   ~OsActivityStreamFlag.HISTORICAL) if unified else log.watch())
                size = 0
                with path.open('w', encoding='utf-8') as f:
                    async for row in rows:
                        if unified:
                            if not row.filename.endswith('/CommCenter'):
                                continue
                            ready.set()
                            line = f'{row.timestamp:%b %d %H:%M:%S} iPhone CommCenter[{row.pid}] <{row.level.name}>: {row.message}'
                        else:
                            line = row.decode(errors='replace') if isinstance(row, bytes) else row
                        if keep(line):
                            # The second field is the phone's name, often the owner's.
                            line = mask_ids(re.sub(r'^(\w{3} +\d+ [\d:]+ )\S+ ', r'\1iPhone ', line))
                            size += len(line)
                            require(size <= 16 * 1024 * 1024, 'Log limit reached')
                            f.write(line + '\n'); f.flush()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if status is not None:
                status['log_error'] = error_line(error)
                status['log_interrupted'] = gid_connection_lost(error)
            with contextlib.suppress(Exception):
                with path.open('a', encoding='utf-8') as f: f.write('LOG ERROR: ' + mask_ids(repr(error)) + '\n')
        finally:
            ready.set()
    task = asyncio.create_task(watch())
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(ready.wait(), 10)
    try:
        yield
    finally:
        await asyncio.sleep(linger)  # let the phone flush the last lines
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def device_log(device, path):
    return syslog_capture(device, path, lambda line: any(k in line.lower() for k in DEVICE_LOG_KEYS))


# atc on the phone logs this when it rejects the computer's Apple components. Seen on Windows with
# some iTunes installs, fixed by reinstalling iTunes; another attempt with the same components fails alike.
GRAPPA_REFUSED = 'Grappa session could not be established'


def grappa_refused(log):
    with contextlib.suppress(OSError):
        return GRAPPA_REFUSED in log.read_text(encoding='utf-8', errors='replace')
    return False


class GrappaRefused(RuntimeError):
    pass


def grappa_hint(cause=''):
    text = ('iPhone rejected this computer\'s Apple components (iPhone log: «' + GRAPPA_REFUSED + '»), '
            'retrying will not help.')
    if sys.platform == 'win32':
        text += (' Reinstall iTunes: remove it (including any Microsoft Store version), install iTunes x64 '
                 'from https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362, '
                 'launch it once and try again. '
                 'A 404 on 4PDA requires registration, sign-in and some forum activity. '
                 'See the README section on Grappa session could not be established.')
    else:
        text += ' Copy the debug block above into a GitHub issue.'
    # The host's own error stays in the message: journals and the debug block keep the real failure.
    return text + (f' Host error: {cause}' if cause else '')


def grappa_failure(error, log):
    # Only the host's own failure: an AFC or Books error inside the pause keeps its cause and retries.
    if isinstance(error, RuntimeError) and 'AirTraffic failure' in str(error) and grappa_refused(log):
        return GrappaRefused(grappa_hint(str(error)))
    return None


# iOS 27.2 beta 3 (24B5099f, #56): atc no longer moves a finished asset out of /var/mobile/Media, so
# neither the export nor the write happens. Earlier 27.2 betas still worked. The stop is decided by
# that refusal in the iPhone log, not by the version: the version only gives a warning up front.
AIRLIFT_REFUSED = 'Failed to move completed file for asset'
AIRLIFT_CLOSED_TEXT = ('iPhone will not move AirTraffic files into the carrier catalog: Apple closed '
                       'this carrier-setting method on this iOS build; retrying will not help. The installed '
                       'profile remains.')
# 27.2 betas are 24B5xxx: beta 1 24B5084k and beta 2 24B5089g still moved assets, beta 3 24B5099f refuses.
AIRLIFT_FIRST_REFUSING_BETA = 5099


def ios_version(info):
    return tuple(int(n) for n in re.findall(r'\d+', str(info.get('ProductVersion', '')))[:2])


def airlift_may_be_closed(info):
    version = ios_version(info)
    if version < (27, 2):
        return False
    beta = re.fullmatch(r'24B(5\d{3})[a-z]', str(info.get('BuildVersion', '')))
    return not (version == (27, 2) and beta and int(beta.group(1)) < AIRLIFT_FIRST_REFUSING_BETA)


def airlift_refused(log, source):
    # Only this run's asset: a stale line about another run's folder says nothing about this one.
    with contextlib.suppress(OSError):
        return any(AIRLIFT_REFUSED in line and source in line
                   for line in log.read_text(encoding='utf-8', errors='replace').splitlines())
    return False


class AirliftClosed(RuntimeError):
    pass


async def transfer(device, run, payload=None, expected=None, recovery=False, before_commit=None):
    run.mkdir(parents=True, exist_ok=False)
    token = os.urandom(10).hex()
    source, link, exported = ('airlift-' + t + '-' + token for t in ('src', 'link', 'saved'))
    final_source = source + '/' + PAYLOAD_PATH if payload is not None else exported
    # Books keeps finished items in Sync/Database/OutstandingAssets_4.sqlite keyed by this ID.
    # A fixed '../../../Library/...' ID matches rows left by an interrupted run and atc then
    # skips the catalog (installOnly). Going through this run's staging folder resolves to the
    # same path (Media/<source>/../../Library = /var/mobile/Library) but is unique per run.
    assets = [(f'../../{source}/p0/p1/p2/link', link),
              (f'../../{source}/../../' + TARGET.removeprefix('/var/mobile/'), exported),
              ('../../' + final_source, link + '/' + DEVICE_FAMILY)]
    journal = {'schema': 1, 'udid_hash': digest(device.udid.encode()), 'target': TARGET,
               'source': source, 'link': link, 'exported': exported,
               'complete': False, 'phase': 'created', 'payload_hash': tree_hash(payload) if payload is not None else None}
    def phase(name, **values):
        journal.update(phase=name, **values)
        save_json(run / 'journal.json', journal)
    phase('created')
    snapshot = None
    async with network_timeout(NETWORK_TRANSFER_SECONDS), afc_service(device) as afc:
        for path in (source, link, exported):
            require(await exists(afc, path) is None, 'Staging path collision')
        leftovers = sorted(n for n in await afc.listdir('/') if n.startswith('airlift-'))
        if leftovers:
            journal['media_leftovers'] = leftovers; save_json(run / 'journal.json', journal)
        stale = await purge_stale_books(afc, run)
        if stale:
            journal['stale_books_removed'] = stale; save_json(run / 'journal.json', journal)
        books, books_existed = await books_snapshot(afc, run)
        books_top = read_json(run / 'books.json')['top']
        mutated = False
        try:
            raw = staging_archive(payload)
            (run / 'staging.zip').write_bytes(raw)
            if payload is not None:
                write_tree_zip(run / 'desired.zip', payload)
            phase('staging', requires_recovery=True)
            mutated = True
            service = await device.start_lockdown_service('com.apple.streaming_zip_conduit')
            try:
                await service.send_plist({'MediaSubdir': source}, fmt=plistlib.FMT_BINARY)
                await service.sendall(raw)
                reply = await asyncio.wait_for(service.recv_plist(), 30)
                require(reply.get('Status') == 'DataComplete', 'Streaming ZIP was rejected')
            finally:
                await service.close()
            node = await afc.stat(source + '/p0/p1/p2/link')
            require(node['st_ifmt'] == 'S_IFLNK' and node.get('LinkTarget') == '../../../' + PARENT[1:],
                    'Staged link mismatch')
            if payload is not None:
                require(await remote_tree(afc, source + '/' + PAYLOAD_PATH) == payload, 'Staged carrier tree mismatch')
            await afc.makedirs('Books/Sync')
            metadata = plistlib.dumps({'Books': [{'Persistent ID': a, 'Item ID': str(i), 'DSID': '1'}
                                      for i, (a, _) in enumerate(assets, 1)]}, fmt=plistlib.FMT_BINARY)
            await afc.set_file_contents('Books/Sync/Books.plist', metadata)
            require(await afc.get_file_contents('Books/Sync/Books.plist') == metadata, 'Books staging mismatch')
            async def pause():
                nonlocal snapshot
                phase('export-check')
                # FileComplete is asynchronous: wait for the directory to appear.
                for _ in range(40):
                    if await exists(afc, exported):
                        break
                    await asyncio.sleep(0.1)
                node = await exists(afc, exported)
                if node is None and recovery and payload is not None:
                    phase('recovery-final-authorized')
                    return
                require(node and node['st_ifmt'] == 'S_IFDIR',
                        'iPhone did not export the current carrier catalog. Do not retry installation: '+recover_hint()+'.')
                phase('original-exported')
                snapshot = await remote_tree(afc, exported)
                write_tree_zip(run / 'original.zip', snapshot)
                phase('backup-saved', original_hash=tree_hash(snapshot))
                if expected is not None:
                    # expected: the exact catalog, or a check for when only part of it is known.
                    require(expected(snapshot) if callable(expected) else snapshot == expected, 'The iPhone carrier catalog changed during the operation. Write cancelled: '+recover_hint()+'.')
                require(await remote_tree(afc, exported) == snapshot, 'Export changed after backup')
                if before_commit is not None:
                    await before_commit()
                phase('final-authorized')
            phase('host-started')
            try:
                async with device_log(device, run / 'device.log'):
                    await host_session(device.udid, assets, pause, run)
            except Exception as error:
                if (refused := grappa_failure(error, run / 'device.log')): raise refused from error
                raise
            # The phone may take several seconds to move the final asset after the session ends.
            for _ in range(150):
                if await exists(afc, final_source) is None:
                    break
                await asyncio.sleep(0.1)
            require(await exists(afc, final_source) is None, 'Final source not consumed; operation unconfirmed')
            phase('placement-observed', complete=True, requires_recovery=False)
        except BaseException as error:
            journal['operation_error'] = error_text(error)
            save_json(run / 'journal.json', journal)
            # Export or final move refused by atc: say so instead of a generic failure that invites retries.
            if (isinstance(error, Exception) and not isinstance(error, (AirliftClosed, GrappaRefused))
                    and airlift_refused(run / 'device.log', source)):
                # Refused before the export arrived: the catalog never left its place.
                untouched = journal['phase'] in ('created', 'staging', 'host-started', 'export-check')
                raise AirliftClosed(AIRLIFT_CLOSED_TEXT + (' The iPhone carrier catalog was not changed.' if untouched else '')
                                    + ' Error: ' + error_text(error)) from error
            raise
        finally:
            if mutated:
                try:
                    # Its own limit: the transfer timeout may already have fired on a dead connection.
                    async with network_timeout(NETWORK_BOOKS_SECONDS):
                        other = await restore_books(afc, books, books_existed, books_top)
                    journal['books_restored'] = True
                    if other: journal['books_other_changes'] = other[:50]
                except Exception as e:
                    journal['books_restored'] = False
                    journal['books_restore_error'] = error_text(e)
                    save_json(run / 'journal.json', journal)
                    if journal.get('complete'):
                        raise RuntimeError('Carrier catalog written and verified; only restoration of '
                                           'Books sync files failed: ' + error_text(e)) from e
                    raise
                save_json(run / 'journal.json', journal)
    # Remote originals and staging identifiers are intentionally retained for recovery.
    return snapshot

async def connect(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    return await asyncio.wait_for(create_using_usbmux(serial=udid, autopair=False, connection_type=CONNECTION), 15)

async def carrier_rows(device):
    from pymobiledevice3.exceptions import MissingValueError
    try:
        rows = await device.get_value(key='CarrierBundleInfoArray') or []
    except MissingValueError:
        # Wi-Fi iPads do not expose this cellular-only lockdown key.
        return []
    for row in rows:
        for key, label in (('InternationalMobileSubscriberIdentity', '<imsi>'), ('IntegratedCircuitCardIdentity', '<iccid>')):
            if re.fullmatch(r'\d{8,}', value := str(row.get(key, ''))):
                LOG_SECRETS[value] = label
    return rows


async def cloud_backup(device):
    # iCloud Backup starts once the phone is locked, charging (a cable to the computer counts) and on Wi-Fi,
    # about a day after the last one; its Books plug-in then rewrites Books/Books.plist mid-stage (issue #44).
    from pymobiledevice3.exceptions import LockdownError
    try:
        values = await device.get_value(domain='com.apple.mobile.backup') or {}
    except LockdownError:
        # Missing or prohibited (MDM) domain: only a hint, never a reason to stop. A dropped link still raises.
        return None
    if not values.get('CloudBackupEnabled'):
        return None
    last = values.get('LastCloudBackupDate')
    if isinstance(last, (int, float)):  # seconds since 2001-01-01 UTC
        last = datetime.fromtimestamp(978307200 + last).astimezone().isoformat(timespec='minutes')
    elif isinstance(last, datetime):
        # plistlib gives a naive datetime in UTC; astimezone() would take it for local time.
        last = (last if last.tzinfo else last.replace(tzinfo=timezone.utc)).astimezone().isoformat(timespec='minutes')
    return {'enabled': True, 'last': last if isinstance(last, str) else None}


def backup_warning(backup, now=None):
    if not backup:
        return None
    text = '  iCloud Backup is enabled'
    if backup.get('last'):
        hours = ((now or datetime.now().astimezone()) - datetime.fromisoformat(backup['last'])).total_seconds() / 3600
        text += f' (last backup {hours:.0f} hours ago)' if hours >= 1 else ' (last backup less than an hour ago)'
    return (text + '. Keep the iPhone unlocked until the operation finishes: while charging '
            'with its screen off, iOS may start a backup and rewrite Books sync files.')


async def device_info(device):
    result = {k: await device.get_value(key=k) for k in
              ('ProductType', 'HardwareModel', 'ProductVersion', 'BuildVersion', 'ActivationState',
               'DeviceClass', 'TelephonyCapability')}
    rows = await carrier_rows(device)
    result['carriers'] = [{k: r[k] for k in ('MCC', 'MNC', 'Slot', 'CFBundleIdentifier', 'CFBundleVersion') if k in r}
                          for r in rows]
    result['cloud_backup'] = await cloud_backup(device)
    return result


def configure_device(info):
    # One paired device per process. Select both paths before constructing archives or binding backups.
    global DEVICE_FAMILY, TARGET, SYSTEM_BUNDLE_DIR, SYSTEM_PREFIX
    DEVICE_FAMILY = 'iPad' if info.get('DeviceClass') == 'iPad' or str(info.get('ProductType', '')).startswith('iPad') else 'iPhone'
    TARGET = PARENT + '/' + DEVICE_FAMILY
    SYSTEM_BUNDLE_DIR = 'System/Library/Carrier Bundles/' + DEVICE_FAMILY
    SYSTEM_PREFIX = '../../../../../../' + SYSTEM_BUNDLE_DIR + '/'


def no_cellular_sim(info, rows):
    if DEVICE_FAMILY != 'iPad' or rows:
        return None
    if info.get('TelephonyCapability') is False:
        return 'This is a Wi-Fi-only iPad without a cellular modem. Carrier profiles are unavailable.'
    return 'iPad reported no active SIM or eSIM. Enable a cellular line and try again.'


def check_trigger(path, sims, targets=(BUNDLE,)):
    require(path.suffix == '.ipcc', 'Trigger must be an IPCC')
    tree = read_tree_zip(path)
    bundles = {n.split('/')[1] for n in tree if n.startswith('Payload/') and len(n.split('/')) > 1
               and n.split('/')[1].endswith('.bundle')}
    require(len(bundles) == 1, 'Trigger must contain exactly one bundle')
    name = bundles.pop()
    inner = {n.removeprefix('Payload/'): v for n, v in tree.items() if n.startswith('Payload/')}
    info, carrier = bundle_info(inner, name)
    require(name not in targets, 'Trigger matches the target profile '+name+'; choose a different IPCC')
    require(info.get('CFBundleIdentifier') != 'com.apple.Viva_kw', 'Viva is not an independent trigger')
    identifiers = carrier.get('SupportedSIMs', [])
    require(identifiers and all(isinstance(s, str) and re.fullmatch(r'\d{5,6}(?:_.*)?', s) for s in identifiers),
            'Unknown SupportedSIMs format in trigger')
    affected = set(identifiers)
    for n, (k, _) in tree.items():
        if k == 'l':
            leaf = n.split('/')[-1]
            require(re.fullmatch(r'\d{5,6}(?:_.*)?', leaf), 'Unexpected trigger symlink')
            affected.add(leaf)
    require(not any(a == s or a.startswith(s + '_') for a in affected for s in sims),
            'Trigger overlaps an installed SIM; select a different carrier')
    return {'bundle': name, 'version': info.get('CFBundleVersion'), 'sha256': digest(path.read_bytes())}

async def install_trigger(device, path, run):
    from pymobiledevice3.services.installation_proxy import InstallationProxyService
    # Override upstream extraction to preserve raw bytes without creating local symlinks.
    class Installer(InstallationProxyService):
        async def _upload_ipcc(self, file_stream, afc_client, dst):
            with zipfile.ZipFile(file_stream) as z:
                for entry in z.infolist():
                    target = dst + '/' + entry.filename
                    await afc_client.makedirs(target if entry.is_dir() else target.rsplit('/', 1)[0])
                    if not entry.is_dir():
                        await afc_client.set_file_contents(target, z.read(entry))
    status = {'ipcc_installation_completed': False, 'log_error': None, 'nr_data_verified': False}
    try:
        async with syslog_capture(device, run / 'commcenter.log', lambda line: 'CommCenter' in line,
                                  status, linger=0, unified=True):
            try:
                async with Installer(device) as installer:
                    await asyncio.wait_for(installer.install_from_local(path), 90)
            except Exception as error:
                if 'InstallProhibited' in f'{type(error).__name__} {error}':
                    raise RuntimeError('iPhone prohibits installation (InstallProhibited). Check Settings → '
                                       'Screen Time → Content & Privacy Restrictions → '
                                       'iTunes & App Store Purchases → Installing Apps: Allow, and device '
                                       'management (MDM) profiles. Nothing on the phone was changed.') from error
                raise
            status['ipcc_installation_completed'] = True
            save_json(run / 'installation.json', status)
            await asyncio.sleep(8)
    except BaseException as error:
        status['installation_error'] = error_line(error)
        raise
    finally:
        save_json(run / 'installation.json', status)
    return status

# AirTraffic protocol follows the MIT-licensed AirLift host sequence.
# Native Apple calls run in a disposable subprocess: a blocked DLL cannot hang recovery.
import ctypes as C
import subprocess
import uuid
from datetime import datetime, timezone

APPLE_DIRS = []
ASSET_SHA256 = '3b47408c5d2deb941d8b94c64724f5b72357dc39911ec9e77b7e4b3ccfb8659f'


def error_text(error):
    # TimeoutError and pymobiledevice3's connection errors often carry no message;
    # Never print or store a bare "Error:". Empty errors fall back to the type name.
    text = str(error).strip()
    if text: return text
    if isinstance(error, TimeoutError): return 'timed out'
    connection = (ConnectionError,)
    with contextlib.suppress(Exception):  # a broken install must not hide the real error behind ImportError
        from pymobiledevice3 import exceptions as errors
        connection += (errors.ConnectionTerminatedError, errors.ConnectionFailedError)
    if isinstance(error, connection): return 'connection to the iPhone was lost'
    return type(error).__name__


def error_line(error):
    # "Type: text" for journals and the final report, without "KeyError: KeyError".
    text = error_text(error)
    return type(error).__name__ + ('' if text == type(error).__name__ else ': ' + text)


def backend_error(error, platform_name=None):
    text = 'AirTraffic backend unavailable: ' + str(error)
    if (platform_name or sys.platform) == 'win32' and '.dll' in str(error).lower():
        text += ('\n\nCause: Apple AirTraffic libraries could not be found or loaded. '
                 'iTunes components may be incomplete or incompatible.'
                 '\nRemove iTunes and reinstall it from the link below:'
                 '\nhttps://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362'
                 '\nA 404 on 4PDA requires registration, sign-in and some forum activity.'
                 '\nAfter installation, restart the computer and run CarrierSIM again.')
    return text


def framed(value):
    print('CARRIER_SWAP_JSON:' + json.dumps(value), flush=True)


def host_backend(platform_name=None):
    platform_name = platform_name or sys.platform
    if platform_name in ('darwin', 'win32'):
        return 'apple'
    if platform_name.startswith('linux'):
        return 'linux-native-atc'
    raise RuntimeError('macOS, Windows and Linux are supported.')


def host_worker(config):
    if host_backend() == 'apple':
        if config.get('probe'):
            raise RuntimeError('--atc-probe is available only on Linux')
        from airtraffic_apple import run_worker
        return run_worker(config.get('udid'), config.get('assets', []), config.get('directories', []), framed)
    from airtraffic_native import run_worker
    return asyncio.run(run_worker(config.get('udid'), config.get('assets', []),
                                  config.get('connection', 'USB'), framed, config.get('probe', False)))


LOCAL_NETWORK_HINT = ('macOS appears to deny this terminal Local Network access: the iPhone rejected its Wi-Fi '
    'connection immediately, and AirTraffic connects directly. Allow access in System Settings → Privacy & Security → '
    'Local Network. macOS does not prompt from tmux, screen or SSH: launch Launch macOS.command '
    '(or CarrierSIM in a packaged build) from Finder or a regular terminal, or connect USB and switch transport to USB (menu item 10). '
    'If already permitted, ensure the iPhone is awake and on the same Wi-Fi network')


class LocalNetworkDenied(RuntimeError):
    pass


# macOS may refuse at once while its Local Network alert is still on screen, and may not show the
# alert at all to a process that exits right after the refusal (TN3179): ask again for this long.
LOCAL_NETWORK_WAIT = 20


async def require_local_network(udid):
    # Only AirTraffic needs it: installs, and rollbacks that write the catalog back. Books goes through AFC.
    if CONNECTION!='Network' or not await asyncio.to_thread(local_network_denied,udid): return
    print(f'macOS appears to deny Local Network access. If the Local Network alert appears, '
          f'choose Allow. Waiting up to {LOCAL_NETWORK_WAIT} s…',flush=True)
    for _ in range(LOCAL_NETWORK_WAIT):
        await asyncio.sleep(1)
        if not await asyncio.to_thread(local_network_denied,udid): return
    raise LocalNetworkDenied(LOCAL_NETWORK_HINT)


# macOS refuses a denied connect in about 1 ms; a real "no route" takes neighbor discovery's seconds.
LOCAL_NETWORK_REFUSAL = 0.5


def usbmux_network_address(udid):
    # pymobiledevice3 drops NetworkAddress from its device list, so ask usbmuxd directly: the system
    # socket, which Apple's MobileDevice uses too. None also when the phone is on USB as well: then
    # MobileDevice picks the cable and the direct Wi-Fi path does not matter.
    import socket
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5); s.connect('/var/run/usbmuxd')
        body = plistlib.dumps({'MessageType': 'ListDevices', 'ProgName': 'CarrierSIM', 'ClientVersionString': VERSION})
        s.sendall(struct.pack('<IIII', 16 + len(body), 1, 8, 1) + body)
        def read(n):
            data = b''
            while len(data) < n:
                chunk = s.recv(n - len(data)); require(chunk, 'usbmuxd closed the connection')
                data += chunk
            return data
        devices = plistlib.loads(read(struct.unpack('<I', read(16)[:4])[0] - 16)).get('DeviceList', [])
    mine = [d.get('Properties', {}) for d in devices if d.get('Properties', {}).get('SerialNumber') == udid]
    if any(p.get('ConnectionType') == 'USB' for p in mine): return None
    return next((sockaddr(p['NetworkAddress']) for p in mine
                 if p.get('ConnectionType') == 'Network' and p.get('NetworkAddress')), None)


def sockaddr(raw):
    # BSD sockaddr: length, family, port, then sockaddr_in or sockaddr_in6 fields.
    import socket
    if raw[1] == socket.AF_INET6:
        return socket.AF_INET6, (socket.inet_ntop(socket.AF_INET6, raw[8:24]), 62078, 0,
                                 int.from_bytes(raw[24:28], sys.byteorder))
    if raw[1] == socket.AF_INET:
        return socket.AF_INET, (socket.inet_ntop(socket.AF_INET, raw[4:8]), 62078)
    return None


def local_network_denied(udid):
    # Over Wi-Fi, Apple's MobileDevice inside the AirTraffic host connects to the phone itself, not
    # through usbmuxd. macOS "Local Network" privacy refuses that in 1 ms with EHOSTUNREACH, and the
    # host then waits silently until its deadline, while pymobiledevice3 (via usbmuxd) keeps working.
    # Only a definite refusal counts: any other outcome lets the run go on. A phone that is really
    # unreachable gives the same errno, but only after neighbor discovery gives up (seconds).
    import socket
    if sys.platform != 'darwin': return False
    started = None
    try:
        address = usbmux_network_address(udid)
        if address is None: return False
        with socket.socket(address[0], socket.SOCK_STREAM) as s:
            s.settimeout(3); started = time.monotonic(); s.connect(address[1])
    except OSError as error:
        return error.errno == 65 and started is not None and time.monotonic() - started < LOCAL_NETWORK_REFUSAL  # EHOSTUNREACH
    except Exception:
        return False
    return False


def host_command():
    return [sys.executable, str(SELF), '--_host']


async def host_session(udid, assets, callback, run):
    config = run/'host-input.json'
    save_json(config, {'udid':udid, 'assets':assets, 'directories':APPLE_DIRS, 'connection':CONNECTION})
    proc = await asyncio.create_subprocess_exec(*host_command(), str(config),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def stderr():
        with (run/'host.stderr').open('wb') as f:
            while data := await proc.stderr.read(4096): f.write(data)
    task = asyncio.create_task(stderr())
    paused = False; result = None; heard = False
    deadline = asyncio.timeout(AIRTRAFFIC_SECONDS)
    try:
        with (run/'host.jsonl').open('wb') as log:
            async with deadline:
                while line := await proc.stdout.readline():
                    heard = True
                    log.write(line); log.flush()
                    if not line.startswith(b'CARRIER_SWAP_JSON:'): continue
                    row = json.loads(line[len(b'CARRIER_SWAP_JSON:'):])
                    if row.get('event') == 'before-final-asset':
                        require(not paused, 'Duplicate AirTraffic pause')
                        await callback(); paused = True
                        proc.stdin.write(b'CONTINUE\n'); await proc.stdin.drain()
                    elif 'ok' in row: result = row
                code = await proc.wait()
        detail = (result or {}).get('error') or ('code ' + str(code))
        require(code == 0 and paused and result and result.get('ok'), 'AirTraffic failure: ' + str(detail))
    except TimeoutError:
        if not deadline.expired(): raise  # a timeout inside the pause (Books, AFC) keeps its own cause
        if not heard and CONNECTION == 'Network':
            # Seen on Wi-Fi: the host never reached atc, attempt after attempt. Without "AirTraffic failed"
            # the error is not transient: the stage is rolled back once and no retry wastes minutes.
            hint = (' A common macOS cause is missing Local Network permission (System Settings → '
                    'Privacy & Security); tmux, screen and SSH cannot prompt for it.' if sys.platform == 'darwin' else '')
            raise RuntimeError(f'AirTraffic did not respond over Wi-Fi within {AIRTRAFFIC_SECONDS} s.{hint} Connect USB, '
                               'switch transport to USB (menu item 10) and try again') from None
        # "AirTraffic failure" keeps it transient: the usual rollback and retry follow.
        raise RuntimeError('AirTraffic failure: ' + ('AirTraffic did not respond' if not heard else
                           'sync did not finish') + f' within {AIRTRAFFIC_SECONDS} s') from None
    finally:
        if proc.returncode is None:
            proc.kill(); await proc.wait()
        await task
        config.unlink(missing_ok=True)

TARGET_BUNDLES = ('Vodafone_hu.bundle',)
SYSTEM_PREFIX = '../../../../../../' + SYSTEM_BUNDLE_DIR + '/'


def load_assets():
    path = ROOT/'assets.zip'
    require(digest(path.read_bytes()) == ASSET_SHA256, 'assets.zip is damaged or has been replaced.')
    tree = read_tree_zip(path)
    return tree


def bundle_link(name):
    require(re.fullmatch(r'[A-Za-z0-9_]+\.bundle', name), 'Invalid bundle name: '+name)
    return ('l', (SYSTEM_PREFIX+name).encode())


SLOT_NAMES = {'kOne': 'SIM 1', 'kTwo': 'SIM 2'}
# Exit code for "written, but iOS did not confirm the chosen bundle"; 2 is argparse's usage error.
UNCONFIRMED = 3
# Exit code of --status for the menu's plan-then-confirm (CARRIERSIM_PLAN=1): nothing would be written.
NOTHING_TO_WRITE = 4
SLOT_CHOICES = {'1': ('kOne',), '2': ('kTwo',), 'all': ('kOne', 'kTwo')}


CONFIG = ROOT / 'bundle.yaml'


def load_bundle_config(path=CONFIG):
    # A tiny subset of YAML: "default: Name" and "MCCMNC: Name", comments with #.
    config = {}
    if path.exists():
        for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = raw.split('#', 1)[0].strip()
            if not line: continue
            match = re.fullmatch(r'["\']?(default|\d{5,6})["\']?\s*:\s*["\']?([A-Za-z0-9_]+?)(?:\.bundle)?["\']?', line)
            require(match, f'{path.name}, line {number}: expected default: Vodafone_hu or 25001: Vodafone_ro '
                           '(MCCMNC without spaces; ASCII bundle name).')
            key, name = match.groups()
            require(key not in config, f'{path.name}, line {number}: duplicate key {key}.')
            config[key] = name + '.bundle'
    config.setdefault('default', BUNDLE)
    return config


# "default" covers only the operators this tool is for; a foreign SIM keeps its own profile
# unless bundle.yaml names its MCCMNC or the user picked its slot explicitly.
HOME_MCC = ('250', '257')


def bundle_for(plmn, config, any_mcc=False):
    if config.get(plmn): return config[plmn]
    if any_mcc or plmn[:3] in HOME_MCC: return config.get('default') or BUNDLE
    return None


# Bundle properties extracted from an IPSW by ios-bundles.github.io; file contents, not device tests.
CATALOG_URL = 'https://ios-bundles.github.io/data.json'
CATALOG_MAX_AGE = 7 * 24 * 3600


def parse_catalog(data):
    # JSON syntax alone is not enough: these values become dictionaries and lookup keys
    # when printing the passport. Validate downloads before replacing a working cache.
    catalog = json.loads(data)
    require(isinstance(catalog, dict), 'Invalid catalog object')
    meta, rows = catalog.get('meta', {}), catalog.get('bundles')
    require(isinstance(meta, dict) and isinstance(rows, list), 'Invalid catalog structure')
    bundles = {}
    for row in rows:
        require(isinstance(row, dict), 'Invalid catalog bundle')
        name = row.get('b')
        require(isinstance(name, str) and bool(name), 'Invalid catalog bundle name')
        require(row.get('ih') is None or isinstance(row['ih'], str), 'Invalid catalog Wi-Fi preference')
        bundles[name] = row
    return {'meta': meta, 'bundles': bundles}


@functools.lru_cache(maxsize=None)
def load_catalog(runs):
    # A fresh cache, else the site, else a stale cache; None when nothing is available. Never fatal.
    cache = Path(runs) / 'bundles.json'
    try:
        fresh = cache.exists() and time.time() - cache.stat().st_mtime < CATALOG_MAX_AGE
        if not fresh:
            import ssl, urllib.request
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                context = ssl.create_default_context()
            with urllib.request.urlopen(CATALOG_URL, timeout=5, context=context) as response:
                data = response.read(8 << 20)
            parse_catalog(data)
            cache.parent.mkdir(parents=True, exist_ok=True)
            # Atomic: a torn file with a fresh mtime would pass as a valid cache for a week.
            tmp = cache.with_suffix('.tmp'); tmp.write_bytes(data); tmp.replace(cache)
    except Exception:
        pass
    try:
        return parse_catalog(cache.read_text(encoding='utf-8'))
    except Exception:
        return None


def catalog_name_error(name, catalog):
    # Bundle names are case-sensitive on the phone; a case-only mismatch would cost a whole install cycle.
    name = name.removesuffix('.bundle')
    if not catalog or name in catalog['bundles']: return None
    same = [b for b in catalog['bundles'] if b.lower() == name.lower()]
    return same and f'Bundle {name} not found, but {same[0]} exists: names are case-sensitive. Use {same[0]}.'


def passport_lines(name, catalog):
    name = name.removesuffix('.bundle')
    if not catalog: return []
    meta = catalog['meta']; r = catalog['bundles'].get(name)
    where = f"iOS {meta.get('ios', '?')}, {meta.get('deviceName', '?')}"
    if r is None:
        import difflib
        near = difflib.get_close_matches(name, catalog['bundles'], 3, 0.6)
        return [f'  {name}: not in the bundle catalog ({where}); it may or may not exist on your iOS build.'
                + (' Similar: ' + ', '.join(near) + '.' if near else '')]
    yn = lambda v, unset='not specified': 'yes' if v is True else 'no' if v is False else unset
    pref = lambda v: {'wifi': 'Wi-Fi', 'ims': 'Wi-Fi', 'cellular': 'cellular'}.get(v, 'not specified')
    reg = str(r.get('reg') or '')
    # International: an exit prefix (+, 00, 011) and a full number; anything else is local to the bundle's country.
    imessage = ('international number' if re.fullmatch(r'(?:\+|00|011)\d{7,}', reg) else
                f'local number {reg} in another country: unreachable from Russian networks; phone-number '
                'reactivation may fail' if reg else 'number not specified')
    lines = [f"  {name} · {r.get('country') or '?'}",
             "    Source: https://ios-bundles.github.io/",
             f"    VoWiFi: home preference — {pref(r.get('ih'))}, roaming — {yn(r.get('wroam'))}"
             + (f", label «{r['wn']}»" if r.get('wn') else ''),
             f"    iMessage/FaceTime: {imessage}",
             f"    Icon: {r.get('lte') or 'LTE'} · VoLTE switch: {'no' if r.get('vs') is False else 'available'}",
             f"    5G: {'hidden' if r.get('sw5g') is False else 'available'} · EVS: {'available' if r.get('evs') else 'no'}"
             f" · supplementary IMS services (XCAP): {yn(r.get('xcap'))}"]
    if r.get('vvm') and r.get('vvm') != 'none':
        lines.append(f"    visual voicemail: service SMS sent to {r.get('beacon') or 'another carrier'}")
    return lines


OPERATORS = {'232-05': 'One', '250-01': 'MTS', '250-02': 'MegaFon', '250-11': 'Yota', '250-20': 'T2',
             '250-99': 'Beeline', '257-01': 'A1', '257-02': 'MTS BY', '257-04': 'life:)'}


def mask_phone(phone):
    clean = re.sub(r'[^\d+]', '', phone) if isinstance(phone, str) else ''
    if not clean.startswith('+') or len(clean) < 8:
        return 'number unavailable'
    code = 2 if clean.startswith(('+7', '+1')) else 4 if clean.startswith(
        ('+375', '+992', '+993', '+994', '+995', '+996', '+998')) else 3
    return f'{clean[:code]} ••• •••{clean[-4:]}'


def sim_line(row, top):
    # What the user can match against Settings: operator, SIM type, ICCID tail, masked number,
    # and the bundle iOS actually loaded (an IMSI link shows up here too).
    slot = row.get('Slot')
    plmn = f"{row.get('MCC', '')}-{row.get('MNC', '')}"
    operator = f'{OPERATORS[plmn]} ({plmn})' if plmn in OPERATORS else plmn
    tray_empty = 'Absent' in str(top.get('SIMTrayStatus', ''))
    embedded = top.get('SIM1IsEmbedded') if slot == 'kOne' else None
    kind = ('eSIM' if embedded or (embedded is None and tray_empty) else
            'physical SIM' if embedded is False else 'type unknown')
    iccid = str(row.get('IntegratedCircuitCardIdentity', ''))
    # Lockdown reports the phone number of one line only; show it for the SIM it belongs to.
    phone = (mask_phone(top.get('PhoneNumber')) if iccid and iccid == str(top.get('IntegratedCircuitCardIdentity', ''))
             else 'number unavailable')
    current = str(row.get('CFBundleIdentifier', '')).removeprefix('com.apple.') or 'unknown'
    return '  ·  '.join((SLOT_NAMES.get(slot, str(slot)), operator, kind,
                         f'ICCID …{iccid[-4:]}' if len(iccid) >= 4 else 'ICCID unavailable', phone, 'current: ' + current))


def output_section(title):
    print('\n  ' + title, flush=True)
    print('  ' + '─' * 50, flush=True)


def print_sim_plan(row, top, target):
    parts = sim_line(row, top).split('  ·  ')
    print('\n  ' + ' · '.join(parts[:3]), flush=True)
    print('    ' + ' · '.join(parts[3:5]), flush=True)
    print(f'    {parts[5]}  →  plan: {target}', flush=True)


def select_sims(rows, config=None, slots=SLOT_CHOICES['all'], any_mcc=True, skipped=None, only_skipped_ok=False):
    # any_mcc=False leaves foreign SIMs without their own bundle.yaml line out; their slots go to skipped.
    selected = []; seen_slots = set(); seen_imsi = set()
    for row in rows:
        mcc, mnc = str(row.get('MCC','')), str(row.get('MNC',''))
        slot, imsi = row.get('Slot'), row.get('InternationalMobileSubscriberIdentity')
        require(slot in ('kOne','kTwo') and slot not in seen_slots, 'Ambiguous SIM slots; write cancelled.')
        seen_slots.add(slot)
        if slot not in slots: continue
        # An empty MCC (locked phone, line off) must not pass for a foreign SIM.
        require(re.fullmatch(r'\d{3}',mcc) and re.fullmatch(r'\d{2,3}',mnc),
                'iPhone did not report the carrier for SIM '+(mcc+mnc or SLOT_NAMES.get(slot,str(slot)))+
                '. Enable the line and unlock the phone.')
        bundle = bundle_for(mcc+mnc, config or {}, any_mcc)
        if bundle is None:
            if skipped is not None: skipped.append(slot)
            continue
        require(re.fullmatch(r'\d{3}',mcc) and re.fullmatch(r'\d{2,3}',mnc) and isinstance(imsi,str) and
                re.fullmatch(r'\d{15}',imsi) and imsi.startswith(mcc+mnc),
                'iPhone did not report a full IMSI for SIM '+mcc+mnc+'. Enable the line and unlock the phone.')
        require(imsi not in seen_imsi, 'The same IMSI is reported in two slots; write cancelled.')
        seen_imsi.add(imsi)
        selected.append({'slot':slot,'plmn':mcc+mnc,'imsi':imsi,'bundle':bundle})
    missing = [SLOT_NAMES[s] for s in slots if s not in seen_slots]
    require(len(slots) > 1 or not missing, missing and missing[0]+' not found on the iPhone. Choose another SIM.')
    # --status only reads: a phone with foreign SIMs alone still shows its skipped-SIM plan.
    if not selected and skipped and only_skipped_ok: return selected
    require(selected or skipped is None or not skipped,
            'No Russian or Belarusian carrier SIMs found. To change a foreign SIM profile, add its MCCMNC '
            'to bundle.yaml or choose it in menu item 7 (--sims 1 or 2).')
    require(selected, 'The phone reported no SIM with an available IMSI.')
    return selected


def make_plan(original, sims):
    desired = dict(original)
    # Signed system bundles match the phone's own firmware; only exact IMSI aliases change.
    for sim in sims:
        n = sim['imsi']
        require(n not in original or original[n][0]=='l', 'Found a file or directory in place of an IMSI symlink.')
        desired[n] = bundle_link(sim['bundle'])
    validate_tree(desired)
    return desired


def imsi_links(tree):
    # This installer creates root-level, 15-digit IMSI aliases, never directories.
    return {n:v for n,v in tree.items() if v[0]=='l' and re.fullmatch(r'\d{15}',n)}


def remove_imsi_links(original, only=None):
    # only: the IMSIs to remove; None removes every IMSI alias.
    links = {n for n in imsi_links(original) if only is None or n in only}
    result = {n:v for n,v in original.items() if n not in links}
    validate_tree(result)
    return result


async def choose_device(udid, wait_seconds=180):
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError, NoDeviceConnectedError
    deadline=time.monotonic()+wait_seconds
    announced=False
    while True:
        try:
            # One phone on Wi-Fi and Ethernet (or IPv4 and IPv6) is listed more than once.
            devices=list(dict.fromkeys(d.serial for d in await list_devices() if d.connection_type==CONNECTION))
        except (OSError, ConnectionError, ConnectionFailedToUsbmuxdError, NoDeviceConnectedError):devices=[]
        if udid and udid in devices:return udid
        if not udid and len(devices)==1:return devices[0]
        require(udid or len(devices)<2,'Multiple iPhones connected. Specify --udid.')
        if not announced:
            print('Waiting for the iPhone over Wi-Fi. Keep it unlocked and on the same network as the computer…'
                  if CONNECTION=='Network' else 'Waiting for the iPhone over USB. Connect and unlock it…',flush=True)
            announced=True
        require(time.monotonic()<deadline,'Connection wait timed out. '+
                ('iPhone not visible over Wi-Fi: connect USB once and enable in Finder/iTunes '
                 'Show this iPhone when on Wi-Fi, then try --wifi again.' if CONNECTION=='Network' else
                 'Check the cable and try again.')+
                (' If iPhone appears in Explorer but not iTunes, the Apple Mobile '
                 'Device USB driver is missing: see the Windows cannot see iPhone section in README.' if sys.platform=='win32' and CONNECTION=='USB' else ''))
        await asyncio.sleep(min(2,max(0,deadline-time.monotonic())))


async def ready_device(udid, wait_seconds):
    from pymobiledevice3 import exceptions as errors
    deadline=time.monotonic()+wait_seconds
    last=None;asked=False;unpaired=0
    while True:
        await choose_device(udid,max(0,deadline-time.monotonic()))
        try:
            device=await connect(udid)
            if not device.paired and CONNECTION=='Network':
                # Trust can only be granted over the cable; Wi-Fi reuses the existing pair record.
                # A Wi-Fi drop during StartSession or TLS also reads as unpaired, so retry a few times.
                await device.close()
                unpaired+=1
                require(unpaired<3 and time.monotonic()<deadline,
                        'This computer is not trusted. Connect USB, run without --wifi '
                        'and tap Trust, then try --wifi again.')
                await asyncio.sleep(2)
                continue
            if not device.paired:
                # No pair record on this computer: without pairing lockdown answers GetProhibited.
                # pymobiledevice3 saves the new record to usbmuxd too, so Apple's AirTrafficHost can use it.
                try:
                    if not asked:
                        print('The iPhone will ask Trust This Computer? '
                              'Tap Trust and enter your passcode.',flush=True)
                        asked=True
                    await device.pair(timeout=max(1,deadline-time.monotonic()))
                    require(await device.validate_pairing(),'Pairing failed. Disconnect the cable and try again.')
                except errors.UserDeniedPairingError:
                    await device.close()
                    raise RuntimeError('Do Not Trust was selected on the iPhone. Reconnect the cable, '
                                       'then tap Trust.') from None
                except BaseException:
                    await device.close();raise
            return device
        except (OSError, errors.ConnectionTerminatedError, errors.PasswordRequiredError,
                errors.NotPairedError, errors.PairingDialogResponsePendingError,
                errors.ConnectionFailedError, errors.InvalidConnectionError) as error:
            if last is None:print('Waiting for unlock, trust and a ready '+
                                  ('Wi-Fi connection…' if CONNECTION=='Network' else 'USB connection…'),flush=True)
            last=error
            if time.monotonic()>=deadline:raise RuntimeError('iPhone not ready: unlock it and confirm trust.') from error
            await asyncio.sleep(2)


def transient_error(error):
    from pymobiledevice3 import exceptions as errors
    if isinstance(error,(ConnectionError,TimeoutError,errors.ConnectionTerminatedError,
                         errors.ConnectionFailedError,errors.InvalidConnectionError)):
        return True
    # Broken pipe, reset, timeout; network down/unreachable, host down/unreachable (macOS, then Windows).
    if isinstance(error,OSError) and error.errno in (32,54,60,104,110,50,51,64,65,
                                                     10050,10051,10054,10060,10064,10065):return True
    # The phone answering without our assets is deterministic: retrying only repeats it.
    if isinstance(error,(GrappaRefused,AirliftClosed)):return False
    return isinstance(error,RuntimeError) and any(t in str(error) for t in
        ('AirTraffic failure','Final source not consumed')) and 'did not confirm the required assets' not in str(error)


async def auto_recover(args,failed,before):
    # A dropped connection must not leave the stage for menu 5: reconnect and recover again (#56).
    for attempt in range(1,args.attempts+1):
        device=await ready_device(args.udid,args.wait_seconds)
        recovery=args.runs/(datetime.now().strftime('%Y%m%d-%H%M%S-')+'auto-recovery-'+uuid.uuid4().hex[:6])
        recovery.mkdir(mode=0o700); save_environment(recovery)
        try:
            await recover_all(device,failed,recovery)
            print('iPhone restored to its original state.',flush=True)
            return
        except Exception as error:
            if attempt==args.attempts or not transient_error(error):
                print('Automatic recovery is incomplete. Log:',recovery,flush=True)
                print('Keep the runs folder and '+recover_hint()+'.',flush=True)
                raise
            print('Connection lost during recovery; retrying…',flush=True)
        except BaseException:
            print('Automatic recovery is incomplete. Log:',recovery,flush=True)
            print('Keep the runs folder and '+recover_hint()+'.',flush=True)
            raise
        finally:await device.close()
        await asyncio.sleep(2)
        # A recovery step that failed is itself a stage: take it too, newest first, as --recover would.
        failed=[p for p in pending(args.runs,args.udid) if args.recover or p not in before]


async def execute_with_retry(args,assets):
    # Once selected, reconnect only to this exact phone, even if a different phone appears.
    args.udid=await choose_device(args.udid,args.wait_seconds)
    if (args.gid1 or args.undo_gid1 or args.tmobile_wholesale or args.remove_tmobile_wholesale or args.cleanup_overrides):
        # Catalog placement is single-shot; only interrupted verification reconnects.
        return await execute_gid(args, assets)
    if args.diagnose or args.watch_call or args.report:
        # Read-only: no retries and no auto-recovery, which would write to the phone.
        return await diagnostics(args)
    active_gid = gid_active(args.runs, args.udid)
    if active_gid and not args.status:
        if args.recover:
            for owner in reversed(gid_pending(args.runs, args.udid)):
                require(CONNECTION == 'USB', 'GID crash recovery requires USB.')
                recovery_args = argparse.Namespace(**vars(args))
                recovery_args.gid1 = None
                recovery_args.undo_gid1 = owner
                await execute_gid(recovery_args, assets)
        elif not args.restore_backup:
            raise RuntimeError('A GID profile is installed or interrupted. Remove it explicitly before another write: '
                               '--undo-gid1 ' + str(active_gid[0]))
    # Before any stage: an install needs AirTraffic. A rollback checks only before its AirTraffic
    # step (recover_stage): stages with only Books left go through AFC and need no direct connection.
    # An unfinished stage stops the install anyway (execute): say that first, not the permission.
    if CONNECTION=='Network' and not args.status and not args.recover and not pending(args.runs,args.udid):
        await require_local_network(args.udid)
    for attempt in range(1,args.attempts+1):
        print(f'\n  Attempt {attempt} of {args.attempts}',flush=True)
        before=set(pending(args.runs,args.udid))
        try:return await execute(args,assets)
        except Exception as error:
            # Roll back after any failure; retry only when a new attempt can change the outcome.
            # --status only reads: its failure must never start a recovery that writes to the phone.
            # Stages left by earlier launches are rolled back only when recovery was asked for.
            # A denied Local Network would stop the auto-recovery's AirTraffic step the same way.
            failed=[] if args.status or isinstance(error,LocalNetworkDenied) else [p for p in pending(args.runs,args.udid) if args.recover or p not in before]
            if failed:
                print('Write failed. Restoring the iPhone to its original state first…',flush=True)
                await auto_recover(args,failed,before)
            if attempt==args.attempts or not transient_error(error):raise
            print('Retrying…',flush=True)
            await asyncio.sleep(2)


# Experimental aliases live in the user catalog; system bundles remain untouched.
GID_TERMINAL = {'undone', 'rolled_back', 'aborted'}
TMOBILE_WHOLESALE = 'TMobile_Wholesale_US.bundle'
GID_ICCID_ALIAS = re.compile(r'([0-9]{5,6})_GID1-((?:[0-9A-F]{2}){1,16})_ID-([0-9]{18,22})')


def gid_connection_lost(error):
    from pymobiledevice3 import exceptions as errors
    return (isinstance(error, (ConnectionError, errors.ConnectionTerminatedError,
                              errors.ConnectionFailedError, errors.InvalidConnectionError)) or
            isinstance(error, OSError) and not isinstance(error, TimeoutError) and transient_error(error))


def normalize_gid1(value):
    value = value.strip()
    if value.lower().startswith('0x'):
        value = value[2:]
    require(bool(re.fullmatch(r'[0-9a-fA-F]{2,32}', value)) and len(value) % 2 == 0,
            'GID1 must contain 1–16 bytes of even-length hexadecimal.')
    return value.upper()


def gid_identities(rows):
    result = {}
    for row in rows:
        slot = row.get('Slot')
        plmn = str(row.get('MCC', '')) + str(row.get('MNC', ''))
        imsi = row.get('InternationalMobileSubscriberIdentity', '')
        iccid = row.get('IntegratedCircuitCardIdentity', '')
        require(slot in SLOT_NAMES and slot not in result, 'Ambiguous active SIM slots.')
        require(re.fullmatch(r'[0-9]{3}', str(row.get('MCC', ''))) and
                re.fullmatch(r'[0-9]{2,3}', str(row.get('MNC', ''))) and
                isinstance(imsi, str) and re.fullmatch(r'[0-9]{15}', imsi) and imsi.startswith(plmn) and
                isinstance(iccid, str) and re.fullmatch(r'[0-9]{18,22}', iccid),
                'Every active SIM must report a full, stable IMSI and ICCID; unlock the device.')
        result[slot] = {'plmn': plmn, 'imsi_hash': digest(imsi.encode()), 'iccid_hash': digest(iccid.encode())}
    require(len({v['imsi_hash'] for v in result.values()}) == len(result) and
            len({v['iccid_hash'] for v in result.values()}) == len(result), 'Duplicate SIM identities.')
    return result


def gid_active(runs, udid):
    return [p.parent for p in sorted(runs.glob('*/gid-alias.json'))
            if (r := read_json(p)).get('udid_hash') == digest(udid.encode()) and r.get('state') not in GID_TERMINAL]


def gid_healthy(record):
    results = record.get('result', {})
    complete = set(results) == set(record.get('identities', {}))
    return (record.get('state') == 'applied_verified' and record.get('ownership_established') is True
            and record.get('catalog_verified') is True and record.get('selection_verified') is True
            and complete and bool(results) and all(result.get('verified') is True and
                (not record.get('iccid_qualified') or result.get('operator', {}).get('verified') is True)
                for result in results.values()))


def gid_pending(runs, udid):
    return [owner for owner in gid_active(runs, udid) if not gid_healthy(read_json(owner / 'gid-alias.json'))]


def gid_print_undo(owner):
    import shlex
    command = [sys.executable, str(SELF), '--undo-gid1', str(owner)]
    print('Explicit undo command:', flush=True)
    print('  ' + (subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)), flush=True)


def gid_noop(args, message, owner=None):
    print(message, flush=True)
    if owner is not None:
        gid_print_undo(owner)
    return NOTHING_TO_WRITE if args.status and os.environ.get('CARRIERSIM_PLAN') else 0


def gid_owner_slot(record, identities):
    original = record.get('identities', {}).get(record.get('slot'))
    return next((slot for slot, identity in identities.items() if identity == original), None)


def gid_owners(runs, device):
    return [(owner, gid_record(owner, device)) for owner in gid_active(runs, device.udid)]


def gid_selected_owners(owners, identities, sims):
    if sims == 'all':
        return owners
    slots = SLOT_CHOICES[sims]
    current = [(owner, saved) for owner, saved in owners if gid_owner_slot(saved, identities) in slots]
    if current:
        return current
    # An inactive/removed profile has no current slot; only then use the saved slot.
    return [(owner, saved) for owner, saved in owners
            if gid_owner_slot(saved, identities) is None and saved.get('slot') in slots]


def gid_wholesale_slots(rows, sims):
    candidates = [row for row in rows if row['Slot'] in SLOT_CHOICES[sims] and
                  row.get('CFBundleIdentifier') in ('com.apple.TMobile_MVNO_US', 'com.apple.TMobile_Wholesale_US')]
    require(candidates, 'No eligible T-Mobile SIM was found. Automatic installation requires a reported '
                       'TMobile_MVNO_US profile; other carriers are not changed.')
    return candidates


def gid_owned_tree(tree, record):
    name = record['alias']
    if not record.get('ownership_established'):
        # A baseline/duplicate failure may predate proof that the alias was absent.
        # Such a journal never owns a pre-existing entry, even with identical bytes.
        return dict(tree)
    written = ('l', bytes.fromhex(record['link_hex']))
    require(name not in tree or tree[name] == written,
            'The alias was changed by another operation; refusing to remove it.')
    desired = dict(tree)
    desired.pop(name, None)
    return desired


def gid_wholesale_aliases(tree):
    aliases = []
    for name, entry in tree.items():
        match = GID_ICCID_ALIAS.fullmatch(name)
        if match is not None:
            LOG_SECRETS[match[3]] = '<iccid>'
            if entry == bundle_link(TMOBILE_WHOLESALE):
                aliases.append(name)
    return sorted(aliases)


def gid_snapshot(run, record, device):
    snapshot = read_tree_zip(run / 'snapshot' / 'original.zip')
    require(tree_hash(snapshot) == record['before_hash'], 'The bound catalog backup is missing or damaged.')
    stage = read_json(run / 'snapshot' / 'journal.json')
    bound(stage, device)
    require(stage.get('original_hash') == record['before_hash'], 'Wrong catalog backup.')
    books = read_tree_zip(run / 'snapshot' / 'books.zip')
    state = read_json(run / 'snapshot' / 'books.json')
    require(tree_hash(books) == state.get('hash') == record.get('books_hash'), 'Wrong Books backup.')
    return snapshot


def gid_record(run, device):
    record = read_json(run / 'gid-alias.json')
    bound(record, device)
    if record.get('kind') == 'wholesale-removal':
        require(record.get('schema') == 1 and record.get('family') == DEVICE_FAMILY and
                record.get('bundle') == TMOBILE_WHOLESALE and record.get('iccid_qualified') is True and
                not record.get('ownership_established'), 'Invalid discovered Wholesale removal journal.')
        aliases = record.get('aliases')
        require(isinstance(aliases, list) and all(isinstance(name, str) for name in aliases),
                'Invalid discovered alias list.')
        if record.get('before_hash'):
            snapshot = gid_snapshot(run, record, device)
            require(bool(aliases) and aliases == gid_wholesale_aliases(snapshot),
                    'Discovered aliases do not match the bound catalog backup.')
        else:
            require(not aliases and record.get('state') in ('planning', 'rollback_required', 'aborted'),
                    'Discovered removal has no authorized catalog backup.')
        return record
    base_alias = record.get('plmn', '') + '_GID1-' + normalize_gid1(record.get('gid1', ''))
    qualified = record.get('iccid_qualified', False)
    require(isinstance(qualified, bool), 'Invalid ICCID qualifier binding.')
    expected_alias = base_alias
    if qualified:
        alias = record.get('alias', '')
        prefix = base_alias + '_ID-'
        require(isinstance(alias, str) and alias.startswith(prefix), 'Invalid ICCID-qualified alias.')
        iccid = alias[len(prefix):]
        identity = record.get('identities', {}).get(record.get('slot'), {})
        require(re.fullmatch(r'[0-9]{18,22}', iccid) and identity.get('plmn') == record.get('plmn') and
                digest(iccid.encode()) == identity.get('iccid_hash'),
                'The alias ICCID does not match the original selected SIM binding.')
        LOG_SECRETS[iccid] = '<iccid>'
        expected_alias += '_ID-' + iccid
        if record.get('ownership_established'):
            require(all(record.get('baseline', {}).get(slot, {}).get('operator', {}).get('verified')
                        for slot in record.get('identities', {})),
                    'The qualified alias has no verified operator baseline.')
    require(record.get('schema') == 1 and record.get('kind') == 'gid1-alias' and
            record.get('family') == DEVICE_FAMILY and record.get('alias_before') is None and
            record.get('alias') == expected_alias, 'Invalid GID alias journal.')
    require(not record.get('ownership_established') or record.get('before_hash'),
            'Owned alias has no verified baseline backup.')
    require(record.get('link_hex') == bundle_link(record.get('bundle', ''))[1].hex(),
            'Saved alias bytes do not match the system bundle binding.')
    if record.get('before_hash'):
        require(record['alias'] not in gid_snapshot(run, record, device),
                'The bound catalog backup already contains the alias.')
    return record


def gid_selection(path, identities, installation, *, require_operator=False):
    require(installation and installation.get('ipcc_installation_completed') and not installation.get('log_error')
            and path.exists(), 'A fresh, complete CommCenter rescan log is required.')
    results = {slot: {'slot': slot, 'plmn': identity['plmn'], 'expected': None,
                      'selected': None, 'verified': False} for slot, identity in identities.items()}
    # Unlike the normal report, every block for a slot must agree and verify.
    text = path.read_text(encoding='utf-8', errors='replace')
    require(text.strip(), 'No fresh CommCenter activity was captured.')
    require(not identities or BUNDLE_BLOCK in text, 'The rescan log contains no observable bundle state.')
    blocks = text.split(BUNDLE_BLOCK)
    for result in results.values():
        index = 1 if result['slot'] == 'kOne' else 2
        for kind in ('Carrier', 'Operator') if require_operator else ('Carrier',):
            observations = []
            for block in blocks:
                paths = re.findall(r'Resolved path\s*:\s*([^\r\n]+)', block)
                links = re.findall(r'Linking Path\s*:\s*([^\r\n]+)', block)
                verification = re.findall(r'Verification Result\s*:\s*([^\r\n]+)', block)
                if any(link.strip() == PARENT + f'/BundleLinks/{kind}{index}Bundle.bundle' for link in links):
                    require(len(paths) == len(links) == 1 and verification == ['Success'],
                            f'Ambiguous or rejected {kind.lower()} slot selection.')
                    resolved = paths[0].strip()
                    leaf = resolved.rsplit('/', 1)[-1]
                    require(re.fullmatch(r'[A-Za-z0-9_]+\.bundle', leaf) and
                            resolved == '/System/Library/Carrier Bundles/' + DEVICE_FAMILY + '/' + leaf,
                            'The rescan did not resolve an untouched system carrier bundle.')
                    observations.append(resolved)
            require(observations and len(set(observations)) == 1,
                    f'Missing or ambiguous {kind.lower()} slot selection.')
            selection = dict(resolved=observations[0], selected=observations[0].rsplit('/', 1)[-1], verified=True)
            if kind == 'Carrier':
                result.update(selection)
            else:
                result['operator'] = selection
    return results


def gid_baseline_key(path, slot, alias=None, *, plmn=None):
    # Name is visible when Best match URL and GID1 are private. Never infer a winner
    # from scores or a resolved bundle name: require an actual chosen lookup.
    text = path.read_text(encoding='utf-8', errors='replace')
    index = 1 if slot == 'kOne' else 2
    evidence = []
    for section in text.split('Matching request kCarrier bundle')[1:]:
        section = section.split('Matching response kCarrier bundle', 1)[0]
        if not re.search(r'Linking Path\s*:\s*[^\r\n]*/Carrier' + str(index) + r'Bundle\.bundle', section):
            continue
        before = section.split(BUNDLE_BLOCK, 1)[0]
        system, separator, user = before.partition('User Bundle Lookup')
        require(separator, 'Missing fresh lookup-key evidence.')
        if "No match in user's, choosing System match" in user:
            chosen = system
        elif re.search(r"choosing [Uu]ser(?:'s)? match", user):
            chosen = user
        else:
            raise RuntimeError('The log does not expose an unambiguous chosen matching key.')
        names = re.findall(r'Returning bundle match: Matches:.*', chosen)
        keys = {name.strip().rsplit('/', 1)[-1] for line in names for name in
                re.findall(r'\[Name:\s*([^,\]]+),', line) if name.strip()}
        require(len(keys) == 1, 'Fresh baseline chose an ambiguous matching key.')
        key = next(iter(keys))
        if alias is not None:
            require(key == alias, 'Fresh baseline chose a different matching key.')
        else:
            match = re.fullmatch(re.escape(plmn) + r'_GID1-([0-9A-F]{2,32})', key)
            require(match is not None, 'The selected baseline is not a plain PLMN/GID1 key; automatic installation stopped.')
            normalize_gid1(match[1])
        evidence.append(key)
    require(evidence and len(set(evidence)) == 1, 'No consistent chosen GID1 matching key was observed for the selected slot.')
    return evidence[0]


def gid_trigger(args, assets, run, info, identities, bundle):
    plmns = {v['plmn'] for v in identities.values()}
    candidates = [args.trigger] if args.trigger else []
    if not args.trigger:
        names = ('AVEA_tr_iPad.ipcc',) if DEVICE_FAMILY == 'iPad' else (
            'AVEA_tr.ipcc', 'Swisscom_ch.ipcc', 'O2_Germany.ipcc')
        for name in names:
            if assets is not None and 'triggers/' + name in assets:
                candidate = run / name
                candidate.write_bytes(assets['triggers/' + name][1])
                candidates.append(candidate)
    for candidate in candidates:
        try:
            status = check_trigger(candidate, plmns, (bundle,))
            if not check_trigger_hardware(candidate, info['HardwareModel'], warn=False):
                continue
        except RuntimeError:
            if args.trigger:
                raise
            continue
        target = run / 'gid-trigger.ipcc'
        raw = candidate.read_bytes()
        require(digest(raw) == status['sha256'], 'Trigger changed after validation.')
        target.write_bytes(raw)
        return target, status
    raise RuntimeError('No independent, non-overlapping, board-signed rescan trigger is available.')


class GidRescanConnectionError(RuntimeError):
    """Catalog placement is known; a disconnected capture still needs fresh proof."""


async def gid_rescan(device, trigger, work, name, identities, *, require_operator=False):
    stage = work / name
    stage.mkdir()
    status = await install_trigger(device, trigger, stage)
    if status.get('log_error') and status.get('log_interrupted'):
        raise GidRescanConnectionError('The CommCenter verification stream lost its connection.')
    results = gid_selection(stage / 'commcenter.log', identities, status, require_operator=require_operator)
    save_json(stage / 'selection.json', results)
    return results


async def gid_checked_rescan(device, trigger, work, name, identities, record, reconnect, expected_catalog, *, readback_name):
    qualified = record.get('iccid_qualified', False)
    verify_selection = record.get('ownership_established', False) or record.get('kind') == 'wholesale-removal'
    readback = work / readback_name
    try:
        current = await transfer(device, readback)
        require(current == expected_catalog, 'Carrier catalog readback mismatch.')
        result = (await gid_rescan(device, trigger, work, name, identities, require_operator=qualified)
                  if identities and verify_selection else {})
        require(gid_identities(await carrier_rows(device)) == identities, 'SIM identities changed during verification.')
        return current, result
    except Exception as error:
        if not isinstance(error, GidRescanConnectionError) and not gid_connection_lost(error):
            raise
        print('Carrier operation interrupted the connection. Reconnecting to verify the placed catalog; no alias reapply.', flush=True)
        record.setdefault('verification_connection_errors', []).append(error_line(error))
        device = await reconnect()
        require(gid_identities(await carrier_rows(device)) == identities, 'SIM identities changed after reconnect.')
        if readback in pending(work.parent, device.udid):
            recovery = work / (readback_name + '-recovery')
            recovery.mkdir()
            await recover_stage(device, readback, recovery, alias_record=record)
        current = await transfer(device, work / (name + '-reconnect-catalog'))
        require(current == expected_catalog, 'The catalog changed during reconnect; verification stopped.')
        result = (await gid_rescan(device, trigger, work, name + '-reconnected', identities, require_operator=qualified)
                  if identities and verify_selection else {})
        require(gid_identities(await carrier_rows(device)) == identities, 'SIM identities changed during fresh verification.')
        return current, result


async def gid_repair_stages(device, owner, record, work, runs):
    roots = {owner.resolve(), *(Path(p).resolve() for p in record.get('work_runs', []))}
    stages = [p for p in pending(runs, device.udid) if p.parent.resolve() in roots]
    if stages:
        await recover_all(device, stages, work, alias_record=record)
    require(not any(p.parent.resolve() in roots for p in pending(runs, device.udid)),
            'Stage or Books recovery remains incomplete.')


async def gid_remove(device, owner, record, work, trigger, runs, final_state, reconnect):
    apply_journal = owner / 'apply' / 'journal.json'
    if record.get('ownership_established') and apply_journal.exists():
        applied = read_json(apply_journal)
        bound(applied, device)
        require(applied.get('payload_hash') == record.get('after_hash'), 'Wrong GID apply journal.')
        if not applied.get('complete') and applied.get('phase') in (
                'created', 'staging', 'host-started', 'export-check', 'original-exported', 'backup-saved'):
            # A failed precommit guard proves our final asset was never authorized. An
            # identical alias added by somebody else is not ours to remove.
            record.update(ownership_established=False, apply_not_authorized=True)
    record.update(state='rollback_required', recovery_run=str(work))
    save_json(owner / 'gid-alias.json', record)
    await gid_repair_stages(device, owner, record, work, runs)
    identities = gid_identities(await carrier_rows(device))
    if identities and record.get('ownership_established'):
        require(trigger is not None, 'A SIM became active during cleanup; retry with the current SIMs.')
        check_trigger(trigger, {v['plmn'] for v in identities.values()}, (record['bundle'],))
    # Another independently owned override may have changed a different SIM since
    # this owner's installation. Preserve its current verified state, not old history.
    before = (await gid_rescan(device, trigger, work, 'undo-baseline', identities,
                               require_operator=record.get('iccid_qualified', False))
              if identities and record.get('ownership_established') else {})
    current = await transfer(device, work / 'undo-snapshot')
    desired = gid_owned_tree(current, record)
    async def stable():
        require(gid_identities(await carrier_rows(device)) == identities,
                'Active SIM identities changed during undo; retry the journal-bound undo.')
    if desired != current:
        await transfer(device, work / 'undo', payload=desired, expected=current, before_commit=stable)
    observed, result = await gid_checked_rescan(device, trigger, work, 'undo-rescan', identities, record, reconnect,
                                              desired, readback_name='undo-readback')
    require(observed == desired and (not record.get('ownership_established') or record['alias'] not in observed),
            'Alias removal could not be verified.')
    original_identity = record.get('identities', {}).get(record.get('slot'))
    for slot, identity in (identities.items() if record.get('ownership_established') else ()):
        expected = before[slot]
        if record.get('ownership_established'):
            if record.get('iccid_qualified'):
                if identity == original_identity:
                    expected = record['baseline'][record['slot']]
            elif identity['plmn'] == record['plmn']:
                original_slot = next((s for s, saved in record.get('identities', {}).items()
                                      if saved == identity), None)
                expected = record.get('baseline', {}).get(original_slot)
        if expected is not None:
            require(result[slot]['resolved'] == expected['resolved'],
                    'The SIM did not return to its baseline or retain its unaffected selection.')
            if record.get('iccid_qualified'):
                require(result[slot]['operator']['resolved'] == expected['operator']['resolved'],
                        'The SIM did not return to its operator baseline or retain its unaffected selection.')
    require(not any(p.parent.resolve() == work.resolve() for p in pending(runs, device.udid)),
            'Books or stage recovery is not verified.')
    record.update(state=final_state if record.get('ownership_established') else 'aborted',
                  alias_removed=record['alias'] not in observed, catalog_verified=True,
                  rollback_verified=True, undo_result=result,
                  undo_selection_status=('no_active_sims' if not identities else
                                         'verified' if record.get('ownership_established') else 'not_owned'),
                  finished_at=datetime.now(timezone.utc).isoformat())
    save_json(owner / 'gid-alias.json', record)


async def gid_remove_discovered(device, owner, record, work, args, assets, info, reconnect, *, initial=False):
    await gid_repair_stages(device, owner, record, work, args.runs)
    if not record.get('before_hash'):
        if not initial:
            record.update(state='aborted', finished_at=datetime.now(timezone.utc).isoformat())
            save_json(owner / 'gid-alias.json', record)
            return
        original = await transfer(device, owner / 'snapshot')
        aliases = gid_wholesale_aliases(original)
        if not aliases:
            record.update(state='aborted', catalog_verified=True, finished_at=datetime.now(timezone.utc).isoformat())
            save_json(owner / 'gid-alias.json', record)
            return
        record.update(state='prepared', aliases=aliases, before_hash=tree_hash(original),
                      books_hash=read_json(owner / 'snapshot' / 'books.json')['hash'])
        save_json(owner / 'gid-alias.json', record)
        print(f"Discovered {len(aliases)} full-ICCID Wholesale rule(s); a fresh local backup is saved.", flush=True)
    identities = gid_identities(await carrier_rows(device))
    trigger = None
    if identities:
        trigger, record['trigger'] = gid_trigger(args, assets, work, info, identities, TMOBILE_WHOLESALE)
        save_json(owner / 'gid-alias.json', record)
    before = (await gid_rescan(device, trigger, work, 'undo-baseline', identities, require_operator=True)
              if identities else {})
    require(gid_identities(await carrier_rows(device)) == identities, 'SIM identities changed before removal.')
    current = await transfer(device, work / 'undo-snapshot')
    desired = dict(current)
    for alias in record['aliases']:
        require(alias not in current or current[alias] == bundle_link(TMOBILE_WHOLESALE),
                'A discovered alias was changed by another operation; refusing to remove it.')
        desired.pop(alias, None)
    async def stable():
        require(gid_identities(await carrier_rows(device)) == identities, 'SIM identities changed before removal.')
    record.update(state='removal_started', recovery_run=str(work))
    save_json(owner / 'gid-alias.json', record)
    if desired != current:
        await transfer(device, work / 'undo', payload=desired, expected=current, before_commit=stable)
    observed, result = await gid_checked_rescan(device, trigger, work, 'undo-rescan', identities, record,
                                              reconnect, desired, readback_name='undo-readback')
    require(observed == desired and not any(alias in observed for alias in record['aliases']),
            'Discovered Wholesale removal could not be verified.')
    affected = {(match[1], digest(match[3].encode()))
                for alias in record['aliases'] if (match := GID_ICCID_ALIAS.fullmatch(alias))}
    for slot, identity in identities.items():
        if (identity['plmn'], identity['iccid_hash']) not in affected:
            require(result[slot]['resolved'] == before[slot]['resolved'] and
                    result[slot]['operator']['resolved'] == before[slot]['operator']['resolved'],
                    'An unaffected SIM changed its carrier or operator selection.')
    # Without the installing computer's history, do not invent an original
    # bundle. Exact alias absence plus fresh, signed system selection is proof.
    require(not any(p.parent.resolve() == work.resolve() for p in pending(args.runs, device.udid)),
            'Books or stage recovery is not verified.')
    record.update(state='undone', catalog_verified=True, rollback_verified=True, undo_result=result,
                  undo_selection_status='verified' if identities else 'no_active_sims',
                  finished_at=datetime.now(timezone.utc).isoformat())
    save_json(owner / 'gid-alias.json', record)


async def execute_gid(args, assets):
    if args.gid1 or args.undo_gid1:
        return await execute_gid_one(args, assets)
    require(CONNECTION == 'USB', 'GID and T-Mobile Wholesale installation/removal/cleanup require USB.')
    device = await ready_device(args.udid, args.wait_seconds)
    try:
        info = await device_info(device)
        configure_device(info)
        require(info.get('DeviceClass') in ('iPhone', 'iPad') and info.get('ActivationState') == 'Activated',
                'An activated iPhone or cellular iPad is required.')
        identities = gid_identities(rows := await carrier_rows(device))
        if args.tmobile_wholesale:
            requests = [('install', row['Slot']) for row in gid_wholesale_slots(rows, args.sims)]
        else:
            owners = gid_owners(args.runs, device)
            if not args.cleanup_overrides:
                owners = [(owner, saved) for owner, saved in owners
                          if saved.get('kind') == 'wholesale-removal' or
                          saved.get('bundle') == TMOBILE_WHOLESALE and saved.get('iccid_qualified')
                          and saved.get('ownership_established')]
            owners = gid_selected_owners(owners, identities, args.sims)
            requests = [('remove', owner) for owner, _ in reversed(owners)]
            if args.sims == 'all':
                requests.append(('discover', None))
            if not requests:
                return gid_noop(args, 'No selected owned overrides were found. Nothing was changed.')
    finally:
        await device.close()
    completed = 0
    noop_only = True
    for action, target in requests:
        current = argparse.Namespace(**vars(args))
        current.cleanup_overrides = current.remove_tmobile_wholesale = False
        if action == 'install':
            current.sims = '1' if target == 'kOne' else '2'
        else:
            current.tmobile_wholesale = False
            current.gid1 = None
            current.undo_gid1 = target
        try:
            code = await execute_gid_one(current, assets, expected_identities=identities, discover=action == 'discover')
        except BaseException:
            if completed and not args.status:
                print(f'{completed} earlier operation(s) completed and were not reversed; '
                      'the failed operation has its own recovery journal.', flush=True)
            raise
        completed += 1
        noop_only = noop_only and code == NOTHING_TO_WRITE
    if args.status and noop_only and os.environ.get('CARRIERSIM_PLAN'):
        return NOTHING_TO_WRITE
    return 0


async def execute_gid_one(args, assets, *, expected_identities=None, discover=False):
    require(CONNECTION == 'USB', 'GID and T-Mobile Wholesale installation/removal require USB.')
    device = await ready_device(args.udid, args.wait_seconds)
    owner = work = record = None
    trigger = None
    mutations_started = False
    automatic = args.tmobile_wholesale
    undo = bool(args.undo_gid1)
    try:
        info = await device_info(device)
        configure_device(info)
        require(info.get('DeviceClass') in ('iPhone', 'iPad') and info.get('ActivationState') == 'Activated',
                'An activated iPhone or cellular iPad is required.')
        DIAG['info'] = info
        print(f"Device: {info['ProductType']} · {info['HardwareModel']} · "
              f"iOS {info['ProductVersion']} ({info['BuildVersion']})", flush=True)

        async def reconnect():
            nonlocal device
            udid = args.udid
            with contextlib.suppress(Exception):
                await device.close()
            device = await ready_device(udid, args.wait_seconds)
            require(device.udid == udid, 'A different device appeared during reconnect.')
            current_info = await device_info(device)
            require(all(current_info.get(key) == info.get(key) for key in
                        ('DeviceClass', 'ProductType', 'HardwareModel', 'BuildVersion', 'ActivationState')),
                    'The device or iOS build changed during this operation.')
            if record is not None:
                bound(record, device)
            return device

        rows = await carrier_rows(device)
        identities = gid_identities(rows)
        if expected_identities is not None:
            require(identities == expected_identities, 'SIM identities changed during the multi-SIM operation.')
        if undo:
            owner = args.undo_gid1.resolve()
            record = gid_record(owner, device)
            discover = record.get('kind') == 'wholesale-removal'
        if discover:
            print('All recognized full-ICCID Wholesale rules will be discovered on this phone,\n'
                  'including other-computer installations and inactive or removed eSIMs.', flush=True)
            if args.status:
                print('Read-only preview: catalog discovery and a fresh local backup occur only after confirmation.\n'
                      'No catalog export, trigger, Books staging or rule removal has occurred.', flush=True)
                return 0
            if not undo:
                require(not pending(args.runs, device.udid) and not gid_pending(args.runs, device.udid),
                        'Recover unfinished operations before discovering additional overrides.')
            work = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'gid-' + uuid.uuid4().hex[:6])
            work.mkdir(mode=0o700)
            DIAG['run'] = work
            save_environment(work)
            if undo:
                record.setdefault('work_runs', []).append(str(work))
            else:
                owner = work
                record = dict(schema=1, kind='wholesale-removal', state='planning', target=TARGET,
                              udid_hash=digest(device.udid.encode()), family=DEVICE_FAMILY,
                              bundle=TMOBILE_WHOLESALE, iccid_qualified=True, aliases=[],
                              identities=identities, work_runs=[str(work)],
                              created_at=datetime.now(timezone.utc).isoformat())
            save_json(owner / 'gid-alias.json', record)
            mutations_started = True
            await gid_remove_discovered(device, owner, record, work, args, assets, info, reconnect, initial=not undo)
            if record['state'] == 'undone':
                print('Discovered Wholesale rules removed; exact catalog and Books restoration verified.', flush=True)
                if record['undo_selection_status'] == 'no_active_sims':
                    print('No active SIMs: selection cannot be tested until a SIM is enabled.', flush=True)
                else:
                    print('Current carrier/operator selections and signatures verified.', flush=True)
            else:
                print('No additional removal was authorized; no matching rule was removed.', flush=True)
            return 0
        if undo:
            print('Undo only the exact alias owned by:', owner, flush=True)
            print('Alias:', mask_ids(record['alias']), '→', record['bundle'], flush=True)
            print('The original SIM may be disabled or removed; current catalog changes are preserved.', flush=True)
            if args.status:
                print('Read-only preview. Owned-entry, catalog and recovery checks run on confirmed undo.', flush=True)
                return 0
            if record.get('state') == 'planning':
                roots = {owner, *(Path(p).resolve() for p in record.get('work_runs', []))}
                require(not record.get('ownership_established') and not record.get('baseline') and
                        not any(p.parent.resolve() in roots for p in pending(args.runs, device.udid)),
                        'A planning journal contains evidence of device changes; recovery stopped.')
                record.update(state='aborted', finished_at=datetime.now(timezone.utc).isoformat())
                save_json(owner / 'gid-alias.json', record)
                print('Operation cancelled before device changes; no alias was committed.', flush=True)
                return 0
        else:
            if automatic:
                selected_rows = gid_wholesale_slots(rows, args.sims)
                require(len(selected_rows) == 1, 'A single installation step must identify exactly one SIM.')
                selected_row = selected_rows[0]
                slot = selected_row['Slot']
                matching = [(path, saved) for path, saved in gid_owners(args.runs, device)
                            if saved.get('bundle') == TMOBILE_WHOLESALE and saved.get('iccid_qualified')
                            and gid_owner_slot(saved, identities) == slot]
                require(len(matching) <= 1, 'Conflicting ownership journals exist for this SIM. Use explicit journal-bound undo.')
                if matching:
                    installed_owner, installed = matching[0]
                    require(gid_healthy(installed), 'This SIM has an interrupted installation. Use --recover or explicit removal first.')
                    require(selected_row['CFBundleIdentifier'] == 'com.apple.TMobile_Wholesale_US',
                            'The owned installation is no longer reported as Wholesale. Remove it explicitly before reinstalling.')
                    print('Recorded catalog and carrier/operator verification is intact; the phone currently reports Wholesale.\n'
                          'No fresh catalog export or rescan was performed.', flush=True)
                    return gid_noop(args, 'Already installed for this SIM. The installation was left unchanged.', installed_owner)
                if selected_row['CFBundleIdentifier'] == 'com.apple.TMobile_Wholesale_US':
                    return gid_noop(args, 'The phone already reports T-Mobile Wholesale without an owned installation. Nothing was changed.')
                args.bundle = TMOBILE_WHOLESALE
                iccid_qualified = True
                base_alias = alias = None
            else:
                slot = SLOT_CHOICES[args.sims][0]
                require(slot in identities, 'The explicitly selected SIM is not active.')
                selected_row = next(row for row in rows if row['Slot'] == slot)
                iccid_qualified = args.gid1_iccid
                base_alias = identities[slot]['plmn'] + '_GID1-' + args.gid1
                alias = base_alias
            chosen = identities[slot]
            require(not airlift_may_be_closed(info), 'This build is known to close AirLift; no installation will be attempted.')
            # Only full ICCID qualification isolates a GID rule from another SIM on the same PLMN.
            require(iccid_qualified or all(s == slot or v['plmn'] != chosen['plmn'] for s, v in identities.items()),
                    'Another active SIM has the same PLMN; an unqualified GID rule could affect both.')
            iccid = selected_row['IntegratedCircuitCardIdentity']
            LOG_SECRETS[iccid] = '<iccid>'
            if iccid_qualified and alias is not None:
                alias += '_ID-' + iccid
            require(gid_identities(await carrier_rows(device)) == identities, 'SIM identifiers are unstable.')
            existing = gid_owners(args.runs, device)
            require(all(gid_healthy(saved) for _, saved in existing),
                    'Recover interrupted GID installations before adding another override.')
            require(not existing or iccid_qualified and all(saved.get('iccid_qualified') for _, saved in existing),
                    'Only full-ICCID-qualified overrides may coexist; remove an unqualified GID rule first.')
            owned_iccids = [saved['identities'][saved['slot']]['iccid_hash'] for _, saved in existing]
            require(len(owned_iccids) == len(set(owned_iccids)) and chosen['iccid_hash'] not in owned_iccids,
                    'Conflicting or duplicate ownership exists for this SIM. Remove its existing override first.')
            require(not pending(args.runs, device.udid), 'Recover unfinished stages before applying a GID installation.')
            print('Selected:', sim_header(selected_row), '· ICCID ending', iccid[-4:], flush=True)
            if automatic:
                print('Target:', TMOBILE_WHOLESALE, '· full ICCID qualification.\n'
                      'The actual matched GID1 key will be discovered from a fresh verified stock baseline; no identifier is guessed.',
                      flush=True)
            else:
                print('Experimental alias:', mask_ids(alias), '→', args.bundle, flush=True)
                print('The qualifier uses this SIM’s full ICCID; carrier and operator selections must both verify.'
                      if iccid_qualified else
                      'The slot identifies the SIM for planning; a PLMN/GID rule is NOT isolated to that slot.', flush=True)
                print('A fresh verified baseline must choose:', base_alias, '· Missing evidence stops safely.', flush=True)
            if args.status:
                print('Read-only preview: no trigger, catalog export, Books backup or write has occurred.', flush=True)
                return 0
        work = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'gid-' + uuid.uuid4().hex[:6])
        work.mkdir(mode=0o700)
        DIAG['run'] = work
        save_environment(work)
        if not undo:
            plan = {'schema': 1, 'kind': 'gid1-alias', 'state': 'planning', 'target': TARGET,
                    'udid_hash': digest(device.udid.encode()), 'family': DEVICE_FAMILY,
                    'slot': slot, 'plmn': chosen['plmn'], 'gid1': args.gid1, 'alias': alias,
                    'iccid_qualified': iccid_qualified, 'automatic_tmobile_wholesale': automatic,
                    'bundle': args.bundle, 'alias_before': None, 'ownership_established': False,
                    'link_hex': bundle_link(args.bundle)[1].hex(), 'identities': identities,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'work_runs': [str(work)]}
            if not automatic:
                owner, record = work, plan
                save_json(owner / 'gid-alias.json', record)
        if warning := backup_warning(await cloud_backup(device)):
            print(warning, flush=True)
        trigger_info = None
        if not undo or identities and record.get('ownership_established'):
            trigger, trigger_info = gid_trigger(args, assets, work, info, identities, record['bundle'] if undo else args.bundle)
        baseline = None
        if automatic:
            # Discovery only installs an independently signed, non-overlapping trigger.
            # Do not create an unrecoverable alias journal with an unknown GID1.
            baseline = await gid_rescan(device, trigger, work, 'baseline', identities, require_operator=True)
            require(gid_identities(await carrier_rows(device)) == identities, 'SIM changed during baseline discovery.')
            stock = '/System/Library/Carrier Bundles/' + DEVICE_FAMILY + '/TMobile_MVNO_US.bundle'
            require(baseline[slot]['operator']['resolved'] == stock and baseline[slot]['resolved'] == stock,
                    'The fresh baseline is not the reported stock T-Mobile MVNO bundle.')
            base_alias = gid_baseline_key(work / 'baseline' / 'commcenter.log', slot, plmn=chosen['plmn'])
            args.gid1 = normalize_gid1(base_alias.split('_GID1-', 1)[1])
            alias = base_alias + '_ID-' + iccid
            print('Discovered matched GID1 key:', base_alias, '· not a raw EF GID1 read.', flush=True)
            owner, record = work, plan
            record.update(state='baseline_verified', gid1=args.gid1, alias=alias,
                          baseline=baseline, baseline_key=base_alias)
        if undo:
            record.setdefault('work_runs', []).append(str(work))
        if trigger_info is not None:
            record['trigger'] = trigger_info
        save_json(owner / 'gid-alias.json', record)
        if undo:
            mutations_started = True
            await gid_remove(device, owner, record, work, trigger, args.runs, 'undone', reconnect)
            print('Owned GID alias removed; catalog and Books restoration verified.' if record.get('ownership_established') else
                  'Uncommitted operation cleaned up; unowned entries were preserved.', flush=True)
            if record['undo_selection_status'] == 'verified':
                print('Current slot selections verified.', flush=True)
            elif record['undo_selection_status'] == 'no_active_sims':
                print('No active SIMs: slot selection cannot be tested until a SIM is enabled.', flush=True)
            return 0
        if not automatic:
            record['state'] = 'baseline_started'
            save_json(owner / 'gid-alias.json', record)
            mutations_started = True
            baseline = await gid_rescan(device, trigger, work, 'baseline', identities, require_operator=iccid_qualified)
            record['baseline'] = baseline
            save_json(owner / 'gid-alias.json', record)
            gid_baseline_key(work / 'baseline' / 'commcenter.log', slot, base_alias)
        mutations_started = True
        original = await transfer(device, work / 'snapshot')
        require(original is not None and alias not in original, 'The alias already exists; refusing duplicate or conflicting ownership.')
        require(gid_identities(await carrier_rows(device)) == identities, 'SIM changed before commit.')
        desired = dict(original)
        desired[alias] = bundle_link(args.bundle)
        validate_tree(desired)
        record.update(state='prepared', baseline=baseline, baseline_key=base_alias, ownership_established=True,
                      before_hash=tree_hash(original), after_hash=tree_hash(desired),
                      books_hash=read_json(work / 'snapshot' / 'books.json')['hash'])
        save_json(owner / 'gid-alias.json', record)
        async def stable():
            require(gid_identities(await carrier_rows(device)) == identities, 'SIM changed before commit.')
        # Intent precedes transfer: a crash after placement still requires scoped undo.
        record['state'] = 'apply_started'
        save_json(owner / 'gid-alias.json', record)
        await transfer(device, work / 'apply', payload=desired, expected=original, before_commit=stable)
        _, result = await gid_checked_rescan(device, trigger, work, 'rescan', identities, record, reconnect,
                                            desired, readback_name='readback')
        for current_slot in identities:
            for kind in ('carrier', 'operator') if iccid_qualified else ('carrier',):
                current = result[current_slot] if kind == 'carrier' else result[current_slot]['operator']
                previous = baseline[current_slot] if kind == 'carrier' else baseline[current_slot]['operator']
                expected = ('/System/Library/Carrier Bundles/' + DEVICE_FAMILY + '/' + args.bundle
                            if current_slot == slot else previous['resolved'])
                require(current['resolved'] == expected,
                        f"SIM {1 if current_slot == 'kOne' else 2} {kind} selected {current['selected']}; "
                        f"expected {expected.rsplit('/', 1)[-1]}.")
        require(not any(p.parent == work for p in pending(args.runs, device.udid)), 'Books recovery is incomplete.')
        record.update(state='applied_verified', result=result, catalog_verified=True, selection_verified=True,
                      verified_at=datetime.now(timezone.utc).isoformat())
        save_json(owner / 'gid-alias.json', record)
        print('Catalog, selection and signature verification succeeded. The installation is left applied.\n'
              'No background program is needed. iOS/carrier updates can replace the rule or close the write path.', flush=True)
        gid_print_undo(owner)
        return 0
    except BaseException as error:
        if record is not None and owner is not None and work is not None:
            record.update(state='aborted' if not mutations_started and not undo else 'rollback_required',
                          operation_error=error_line(error))
            save_json(owner / 'gid-alias.json', record)
            if record['state'] == 'aborted':
                print('Operation stopped before device changes; no recovery is needed.', flush=True)
            # Never repeat apply. Reconnect before scoped rollback so cleanup owns a live client.
            if mutations_started and (trigger is not None or undo or discover):
                try:
                    await reconnect()
                    recovery = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'gid-rollback-' + uuid.uuid4().hex[:6])
                    recovery.mkdir(mode=0o700)
                    record['work_runs'].append(str(recovery))
                    save_json(owner / 'gid-alias.json', record)
                    if discover:
                        await gid_remove_discovered(device, owner, record, recovery, args, assets, info, reconnect)
                    else:
                        await gid_remove(device, owner, record, recovery, trigger, args.runs,
                                         'undone' if undo else 'rolled_back', reconnect)
                    print('Scoped rollback verified; unrelated current catalog entries were preserved.', flush=True)
                except BaseException as rollback_error:
                    record.update(state='rollback_required', rollback_error=error_line(rollback_error))
                    save_json(owner / 'gid-alias.json', record)
                    print('Recovery is incomplete. Keep runs and use --undo-gid1 ' + str(owner), file=sys.stderr)
        raise
    finally:
        await device.close()


BUNDLE_BLOCK = '----------Bundle File----------'


def report_log(path, sims):
    results = {s['slot']:{'slot':s['slot'],'plmn':s['plmn'],'expected':s['bundle'],
                         'selected':None,'verified':False} for s in sims}
    if path.exists():
        for block in path.read_text(encoding='utf-8',errors='replace').split(BUNDLE_BLOCK):
            resolved = re.findall(r'Resolved path\s*:\s*([^\r\n]+)',block)
            linked = re.findall(r'Linking Path\s*:\s*([^\r\n]+)',block)
            verified = re.findall(r'Verification Result\s*:\s*([^\r\n]+)',block)
            if len(resolved)!=1 or len(linked)!=1: continue
            for slot,index in (('kOne',1),('kTwo',2)):
                if slot in results and linked[0].strip().endswith(f'/Carrier{index}Bundle.bundle'):
                    results[slot].update(selected=resolved[0].strip().rsplit('/',1)[-1],
                                         verified=verified==['Success'])
    return list(results.values())


def slot_outcome(result, ok):
    # One line per SIM after the rescan; the wording is what users paste into bug reports.
    expected = (result['expected'] or '').removesuffix('.bundle')
    selected = (result['selected'] or '').removesuffix('.bundle')
    if ok: return (selected or 'stock profile') + ' — signature accepted'
    if not selected: return 'rescan log contains no bundle selection for this SIM'
    if expected and selected.lower() != expected.lower(): return f'iOS selected {selected} instead of {expected}'
    return f'{selected} selected, but signature rejected'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def pending(runs, udid):
    # Oldest first: run folders start with a timestamp; stages inside one run by journal age.
    journals = sorted(runs.glob('*/*/journal.json'), key=lambda p: (p.parent.parent.name, p.stat().st_mtime))
    return [p.parent for p in journals
            if (j:=read_json(p)).get('udid_hash')==digest(udid.encode())
            and (j.get('requires_recovery') or j.get('books_restored') is False) and not j.get('recovered_by')]


@contextlib.contextmanager
def operation_lock(runs):
    runs.mkdir(parents=True,exist_ok=True)
    with (runs/'.lock').open('a+b') as f:
        # Append mode: write the lockable byte only once, not on every launch.
        if f.seek(0,2)==0: f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('CarrierSIM is already running in another window. Wait for it to finish or close it.') from None
        try: yield
        finally:
            if sys.platform=='win32':
                f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)


def bound(record,device):
    require(record.get('target')==TARGET and record.get('udid_hash')==digest(device.udid.encode()),
            'Backup belongs to another device or catalog.')


EXPORT_SETTLE_SECONDS = 60


async def recover_stage(device, failed, run, tag='', alias_record=None):
    record = read_json(failed/'journal.json'); bound(record,device)
    if record.get('recovered_by'):
        print('Stage already recovered:',record['recovered_by'],flush=True)
        return
    remote = record.get('exported','')
    require(re.fullmatch(r'airlift-saved-[a-f0-9]{20}',remote),'Invalid recovery path.')
    books = read_tree_zip(failed/'books.zip'); state=read_json(failed/'books.json')
    require(tree_hash(books)==state['hash'],'Books backup is damaged.')
    desired = None
    async with afc_service(device) as afc:
        if alias_record and not record.get('complete') and record.get('phase') == 'final-authorized':
            final = (record['source'] + '/' + PAYLOAD_PATH if record.get('payload_hash') else record['exported'])
            require(await exists(afc, final) is None,
                    'GID placement is still ambiguous. Keep runs; use the explicit full --restore-backup fallback if journal-bound undo cannot recover.')
            # Placement finished before the host could persist complete=True. Never replace
            # the whole catalog here: the following bound undo reads it and removes one alias.
            record['complete'] = True
            record['placement_recovered'] = True
        if record.get('complete'):
            # The carrier stage finished; only the Books cleanup failed. Never roll back the catalog.
            if (other:=await restore_books(afc,books,state['existed'],state.get('top'))):record['books_other_changes']=other[:50]
            record['recovered_by']=str(run);record['books_restored']=True
            save_json(failed/'journal.json',record)
            return
        if record.get('phase') in ('host-started','export-check') and not await exists(afc,remote):
            # The export asset may already be sent: the host sends it before the pause, and
            # FileComplete is asynchronous. Give atc time to finish the move before deciding
            # that the catalog never left its place.
            for _ in range(EXPORT_SETTLE_SECONDS):
                if await exists(afc,remote): break
                await asyncio.sleep(1)
            else:
                record['export_absent_after_wait']=EXPORT_SETTLE_SECONDS
        if await exists(afc,remote):
            desired = await remote_tree(afc,remote)
            if record.get('original_hash'):
                require(tree_hash(desired)==record['original_hash'],'Remote backup changed.')
        elif (failed/'original.zip').exists():
            desired = read_tree_zip(failed/'original.zip')
            require(tree_hash(desired)==record.get('original_hash'),'Local backup is damaged.')
        else:
            # No exported copy on the phone (waited for above) and none saved locally: the catalog
            # was never moved out, and the final asset is only sent after the backup is saved.
            # Only Books and the staging folders in /var/mobile/Media changed: Books is put back
            # here, the staging folders are removed by the next run's cleanup (clean_phone).
            require(record.get('phase') in ('created','staging','host-started','export-check'),
                    'No verified backup. Keep runs; recovery stopped.')
        if (other:=await restore_books(afc,books,state['existed'],state.get('top'))):record['books_other_changes']=other[:50]
    if desired is not None:
        await require_local_network(device.udid)
        write_tree_zip(run/f'recovery-original{tag}.zip',desired)
        await transfer(device,run/f'recover{tag}',payload=desired,recovery=True,
                       expected=desired if alias_record is not None else None)
        observed=await transfer(device,run/f'readback{tag}')
        require(observed==desired,'Restored catalog differs from the backup.')
    record['recovered_by']=str(run);record['requires_recovery']=False;record['books_restored']=True
    save_json(failed/'journal.json',record)


async def recover_all(device, stages, run, alias_record=None):
    # Undo newest first: a failed recovery attempt is itself a stage on top of the one it repaired.
    for i,failed in enumerate(reversed(stages),1):
        print('Recovering stage:',failed,flush=True)
        await recover_stage(device,failed,run,f'-{i}' if len(stages)>1 else '', alias_record=alias_record)


def check_trigger_hardware(path, hardware, warn=True):
    tree = read_tree_zip(path)
    board = hardware.upper().removesuffix('AP')
    for name,(kind,_) in tree.items():
        leaf = name.rsplit('/',1)[-1]
        if kind!='f' or '/signatures/' in name or not leaf.startswith('overrides_') or not leaf.endswith('.plist'):
            continue
        boards = leaf.removeprefix('overrides_').removesuffix('.plist').upper().split('_')
        if board in boards:
            signature = name.rsplit('/',1)[0]+'/signatures/'+leaf
            if signature in tree:
                return True
            break
    if warn:
        print('Warning: IPCC contains no signed overrides for board '+hardware+
              '. Rescan MAY not work; continuing.', flush=True)
    return False



# Read-only telephony diagnostics from the CommCenter log stream (os_trace_relay).
# Note: patterns come from iOS 27.x CommCenter strings seen in rescan logs; Apple
# does not document them, so a future iOS may rename them. Raw masked log is saved
# next to the report so the patterns can be updated.
DIAG_PATTERNS = {
    'ims_voice': re.compile(r'IMS Voice registered: (true|false)'),
    'ims_over_wifi': re.compile(r'IMS registered\s*:\s*(true|false)\s*,\s*Over Wifi\s*:\s*(true|false)'),
    'vowifi_pref': re.compile(r'VoWiFi, user preference status is (\w+).*?service status: (\w+)'),
    'vowifi_config': re.compile(r'VoWiFi configuration is: (\w+) \(preferred in roaming: (\w+)\)'),
    'features': re.compile(r'VoLTE Feature support: (\w+), VoNR Feature support: (\w+), VoWiFi Feature support: (\w+)'),
    'roaming': re.compile(r'Is device roaming: (kRoaming|kNotRoaming)\b'),
    'reg_status': re.compile(r'Registration status is (k\w+)'),
    'rat': re.compile(r'(?:current RAT \(|RAT remains at |current RAT set to )(kRat\w+)'),
    'data_mode': re.compile(r'(?:current DataMode set to |Data mode - )(k\w+)'),
    'plmn': re.compile(r'(kRat\w+) PLMN: (\d{3}-\d{2,3})'),
    'signal': re.compile(r'Rsrp=\{y=(-?[\d.]+) : \d+\}, Sinr=\{y=(-?[\d.]+)'),
    'wifi_name': re.compile(r"Operator name is being overridden to '([^']+)'"),
    'sa': re.compile(r'5G Standalone (enabled|disabled)(?: by (\w+))?'),
    'ims_reg': re.compile(r'UE is Registered for ([\w+]+) on (\w+)'),
    'call_status': re.compile(r'slot k\w+ call status (\w+)'),
    # VoWiFi tunnel: NetworkExtension's IKEv2 client runs inside CommCenter and logs to the same stream.
    'epdg': re.compile(r'<NEIKEv2Transport> UDP(?: NAT-T)? \S+ -> (\S+):(?:500|4500)\b'),
    'ike_state': re.compile(r'IKEv2IKESA\[[^\]]*\] state (\w+) -> (\w+)'),
    'ike_error': re.compile(r'NEIKEv2ErrorDomain Code=(\d+) "([^"]*)"'),
}
# P-Access-Network-Info in SIP says which radio carried the call.
SIP_ACCESS = {'IEEE-802.11':'Wi-Fi (VoWiFi)','3GPP-E-UTRAN':'LTE (VoLTE)','3GPP-E-UTRAN-FDD':'LTE (VoLTE)',
              '3GPP-E-UTRAN-TDD':'LTE (VoLTE)','3GPP-NR':'5G (VoNR)','3GPP-NR-FDD':'5G (VoNR)',
              '3GPP-NR-TDD':'5G (VoNR)','3GPP-UTRAN-FDD':'3G'}


CODEC_NAMES = {'EVS/16000':'EVS (HD Voice+)','AMR-WB/16000':'AMR-WB (HD Voice)',
               'AMR/8000':'AMR-NB (standard quality)','PCMA/8000':'G.711 A-law (standard quality)',
               'PCMU/8000':'G.711 µ-law (standard quality)'}


def sip_answer_codec(message):
    # An SDP answer lists exactly one voice codec (plus telephone-event); offers list several.
    m = re.search(r'^\s*m=audio \d+ RTP/AVP ([\d ]+)', message, re.M)
    if not m:
        return None
    maps = dict(re.findall(r'a=rtpmap:(\d+) ([\w.-]+/\d+)', message))
    voice = [maps[p] for p in m.group(1).split() if p in maps and not maps[p].startswith('telephone-event')]
    return voice[0] if len(voice) == 1 else None


def mask_log(text):
    # Phone numbers, IMSI/ICCID and other long identifiers never reach disk or screen.
    return re.sub(r'\+?\d[\d ()-]{6,}\d', '<num>', text)


def mask_ids(text):
    # Raw syslog keeps its numbers (frequencies, TAC, hex dumps, airlift tokens): only the SIMs' own IMSI/ICCID,
    # any other standalone 11+ digit number (IMSI links of earlier SIMs, phones) and +phones are masked.
    for value, label in LOG_SECRETS.items():
        text = text.replace(value, label)
    return re.sub(r'(?<![\w.+-])\d{11,20}(?![\w.])|(?<!\d)\+\d[\d ()-]{6,}\d', '<num>', text)


def log_slot(entry):
    # CommCenter prefixes per-subscription lines with "<slot>.<n> "; categories may end in the slot.
    # Heuristic, unverified on every iOS; unmatched lines are reported as "general".
    if m := re.match(r'([12])\.\d+\s', entry.message):
        return {'1':'kOne','2':'kTwo'}[m.group(1)]
    if m := re.search(r'\bslot (kOne|kTwo)\b', entry.message):
        return m.group(1)
    # Categories end in ".<slot>" (reg.ctr.2, sig.mav5.1) or ".<slot>.<n>" (sip.dump.ims.1.4).
    if entry.label and (m := re.search(r'\.([12])(?:\.\d+)?$', entry.label.category or '')):
        return {'1':'kOne','2':'kTwo'}[m.group(1)]
    return None


def sip_assembler():
    # CommCenter logs each SIP line as its own entry in category sip.dump.*:
    # "==== src --> dst METHOD ====", the message lines, then a "=====" rule.
    # Returns feed(entry) -> (first SIP line, access network, answered codec) once a message completes.
    bufs = {}
    def feed(entry):
        cat = entry.label.category if entry.label else ''
        if not (cat or '').startswith('sip.dump'):
            return None
        line = entry.message.strip()
        if re.fullmatch(r'=+', line):
            if cat not in bufs:
                return None
            msg = '\n'.join(bufs.pop(cat))
            cseq = re.search(r'^CSeq: \d+ (\w+)', msg, re.M)
            if not cseq or cseq.group(1) not in ('INVITE', 'PRACK', 'UPDATE', 'ACK', 'BYE', 'CANCEL'):
                return None  # registration, presence and SMS traffic is not a call
            first = next((l for l in msg.splitlines() if l.strip()), '')
            first = re.sub(r'^(\w+) \S+ SIP/2\.0$', r'\1', first)  # drop request URI (holds the number)
            access = re.search(r'P-Access-Network-Info: ([\w.-]+)', msg)
            return first, access and SIP_ACCESS.get(access.group(1), access.group(1)), sip_answer_codec(msg)
        elif line.startswith('='):
            bufs[cat] = []  # "==== src --> dst ... ====" header, either direction
        elif cat in bufs:
            bufs[cat].append(line)
        return None
    feed.reset = bufs.clear  # after a log drop a half-read message must not merge with new lines
    return feed


# syslog() pid meaning "every process": the CommCenter lines are then picked by executable path.
ALL_PROCESSES = -1


async def commcenter_pid(device, first=False):
    from pymobiledevice3.exceptions import ConnectionTerminatedError
    from pymobiledevice3.services.os_trace import OsTraceService
    try:
        async with OsTraceService(device) as service:
            pids = (await service.get_pid_list()).get('Payload', {})
    except ConnectionTerminatedError:
        # Over Wi-Fi iOS 27 closes the socket on the ~16 KB process list every time,
        # while the unfiltered log itself streams fine. Only before the first stream:
        # on a reconnect the same error just means the relay is still down.
        if not first: raise
        print('iPhone did not provide a process list; collecting all logs and filtering CommCenter on this computer.', flush=True)
        return ALL_PROCESSES
    return next((int(p) for p, v in pids.items() if v.get('ProcessName') == 'CommCenter'), None)


# How long the log may stay unreachable after a drop before the collection gives up.
LOG_RECONNECT_SECONDS = 20


class LogSinkError(Exception):
    """A local failure (disk, parser) that must not look like a dropped log stream."""


async def commcenter_stream(device, seconds, log_path, on_entry, stop=None):
    from pymobiledevice3.exceptions import ConnectionTerminatedError
    from pymobiledevice3.services.os_trace import OsTraceService
    pid = await commcenter_pid(device, first=True)
    require(pid is not None, 'CommCenter process not found on the iPhone.')
    # Airplane mode, which the instructions ask for, can end the log relay on the phone.
    # Reconnect until the deadline instead of losing the whole collection.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    reconnects = 0; down_since = None; received = 0
    stopped = False
    with log_path.open('w', encoding='utf-8') as f:
        while not stopped and not (stop and stop()) and (left := deadline - loop.time()) > 0:
            window = asyncio.timeout(left)
            # After a drop the reconnect itself is bounded too: a hung connect over Wi-Fi must not eat the rest.
            budget = asyncio.timeout(None if down_since is None else
                                     max(0, down_since + LOG_RECONNECT_SECONDS - loop.time()))
            try:
                async with window, budget:
                    if down_since is not None and pid != ALL_PROCESSES:
                        # CommCenter may have restarted: look its pid up again.
                        pid = await commcenter_pid(device)
                        if pid is None: raise ConnectionTerminatedError('CommCenter is not running yet')
                    async with OsTraceService(device) as service:
                        async for e in service.syslog(pid=pid):
                            if down_since is not None:
                                reconnects += 1; down_since = None; budget.reschedule(None)
                            if pid == ALL_PROCESSES and not e.filename.endswith('/CommCenter'): continue
                            received += 1
                            msg = mask_log(e.message)
                            cat = f'{e.label.subsystem}:{e.label.category}' if e.label else '-'
                            try:
                                f.write(f'{e.timestamp:%H:%M:%S} [{cat}] {msg}\n')
                                on_entry(e, msg)
                            except Exception as error:
                                raise LogSinkError() from error
                            # Enter or "everything needed has arrived": end like the deadline does.
                            if stop and stop():
                                stopped = True; break
                    if stopped: break
                    # pymobiledevice3 11.12.5 never ends syslog(); a finite stream would count as a drop.
                    raise ConnectionTerminatedError('log relay ended')
            except LogSinkError as error:
                raise error.__cause__
            except Exception as error:
                # Our own deadline, not a socket timeout (which Python also reports as TimeoutError).
                if isinstance(error, TimeoutError) and window.expired(): break
                # The reconnect budget is spent: as its timeout, or by the clock when failures never yield to the loop.
                gave_up = isinstance(error, TimeoutError) and budget.expired()
                if not gave_up:
                    if not transient_error(error): raise
                    if down_since is None:
                        down_since = loop.time()
                        if reset := getattr(on_entry, 'reset', None): reset()  # drop half-read SIP messages
                        print('iPhone log stream interrupted (possible in airplane mode); reconnecting…', flush=True)
                    gave_up = loop.time() - down_since > LOG_RECONNECT_SECONDS
                if gave_up:
                    # Over Wi-Fi airplane mode may take the whole connection down: keep what was collected.
                    if received: break
                    raise RuntimeError(f'iPhone log unavailable for more than {LOG_RECONNECT_SECONDS} s. '
                                       'Check USB or Wi-Fi and try again.') from error
                await asyncio.sleep(min(1, max(0, deadline - loop.time())))
    # CommCenter writes thousands of lines a minute: nothing at all means no log, not a quiet phone.
    require(received, f'No CommCenter log received within {seconds} s. Check USB or Wi-Fi and try again.')
    if down_since is not None:
        print('Log stream did not resume' + ('' if loop.time() >= deadline else f' within {LOG_RECONNECT_SECONDS} s') +
              '. Report contains only the entries received.', flush=True)
    elif reconnects:
        print(f'Log interrupted; reconnects: {reconnects}. Report contains only the entries received.', flush=True)


def diag_collect(state):
    feed_sip = sip_assembler()
    def on_entry(e, msg):
        # Returns the completed SIP message summary, if this entry finished one.
        slot = log_slot(e) or 'general'
        for key, pat in DIAG_PATTERNS.items():
            if m := pat.search(msg):
                state.setdefault(slot, {})[key] = m.groups()
        if sip := feed_sip(e):
            _, access, codec = sip
            if access:
                state.setdefault(slot, {})['call_access'] = (access,)
            if codec:
                state.setdefault(slot, {})['codec'] = (codec,)
        return sip
    on_entry.reset = feed_sip.reset
    return on_entry


def sim_header(row):
    return f"{SLOT_NAMES[row['Slot']]}  ·  {row.get('MCC', '')}{row.get('MNC', '')}"


def epdg_text(address):
    # A loopback or 0.0.0.0 gateway never reaches the operator: a DNS filter answered for the ePDG name.
    try:
        ip = ipaddress.ip_address(address.strip('[]'))
    except ValueError:
        return address
    if ip.is_loopback or ip.is_unspecified:
        return (f'{address} — placeholder, not a carrier address: the ePDG name is usually blocked by DNS '
                '(router, ad filter, VPN or carrier)')
    return address


def diag_report(state, rows):
    yes = lambda v: {'true':'yes','false':'no','kTrue':'yes','kFalse':'no'}.get(v, v)
    rat = lambda v: v and {'kRatGSM':'2G (GSM)','kRatUMTS':'3G (UMTS)','kRatLTE':'4G (LTE)','kRatNR':'5G (NR)'}.get(v, v)
    reg = lambda v: v and {'kRegisteredHome':'on home network','kRegisteredRoaming':'roaming',
                           'kNotRegistered':'not registered','kRegistrationDenied':'network denied registration',
                           'kSearching':'searching'}.get(v, v)
    lines = []
    for slot in [r.get('Slot') for r in rows if r.get('Slot') in ('kOne','kTwo')] + ['general']:
        s = state.get(slot, {})
        if slot == 'general' and not s:
            continue
        head = sim_header(next(r for r in rows if r.get('Slot') == slot)) \
            if slot != 'general' else 'Not tied to a SIM (slot not identified in the log)'
        lines.append('\n  ' + head)
        g = lambda k, i=0: s[k][i] if k in s and s[k][i] else None
        items = [
            ('IMS', g('ims_reg') and f"{g('ims_reg')} via {g('ims_reg',1)}"),
            ('IMS (voice)', yes(g('ims_voice'))),
            ('IMS over Wi-Fi', g('ims_over_wifi') and f"registration: {yes(g('ims_over_wifi'))}, Wi-Fi: {yes(g('ims_over_wifi',1))}"),
            ('VoWiFi', g('vowifi_pref') and f"setting: {g('vowifi_pref')}, service: {g('vowifi_pref',1)}"),
            ('VoWiFi from', g('vowifi_config') and f"{g('vowifi_config')}, preferred when roaming: {yes(g('vowifi_config',1))}"),
            ('Support', g('features') and f"VoLTE {yes(g('features'))}, VoNR {yes(g('features',1))}, VoWiFi {yes(g('features',2))}"),
            ('Wi-Fi Calling label', g('wifi_name') and f"«{g('wifi_name')}» (VoWiFi active)"),
            ('Registration', reg(g('reg_status'))),
            ('Roaming', g('roaming') and {'kRoaming':'yes','kNotRoaming':'no'}.get(g('roaming'), g('roaming'))),
            ('Network', rat(g('rat'))),
            ('Data', g('data_mode') and g('data_mode').removeprefix('k')),
            ('Serving network', g('plmn') and f"{g('plmn',1)} ({rat(g('plmn'))})"),
            ('LTE signal', g('signal') and f"RSRP {float(g('signal')):.0f} dBm, SINR {float(g('signal',1)):.1f} dB"),
            ('5G SA', g('sa') and (g('sa') == 'enabled' and 'enabled' or f"disabled ({g('sa',1) or 'reason not reported'})")),
            ('Call via', g('call_access')),
            ('Call codec', g('codec') and CODEC_NAMES.get(g('codec'), g('codec'))),
            ('ePDG', g('epdg') and epdg_text(g('epdg'))),
            ('IKE with ePDG', g('ike_state') and f"{g('ike_state')} → {g('ike_state',1)}"),
            ('IKE error', g('ike_error') and f"code {g('ike_error')}: {g('ike_error',1)}"),
        ]
        shown = [(name, val) for name, val in items if val]
        for name, val in shown:
            lines.append(f'    {name:20} {val}')
        if len(shown) < len(items):
            lines.append('    other fields: not present in this log interval')
    return '\n'.join(lines)


def enter_pressed():
    # Enter ends the collection early. Only for actions that read nothing from the keyboard
    # afterwards: the reader thread stays blocked in readline until the process exits.
    if not sys.stdin or not sys.stdin.isatty():
        return None
    pressed = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), pressed.set()), daemon=True).start()
    return pressed.is_set


# After the airplane cycle IMS registers first; the VoWiFi tunnel (IKE with ePDG) may follow later.
DIAG_SETTLE_SECONDS = 10


AIRPLANE_CHANGE = re.compile(r'Airplane mode changed from (?:true|false) to (true|false)')


def registered_again(state, rows, collect):
    # The diagnosis is complete once every SIM re-registered in IMS after airplane mode was turned off,
    # plus a pause for the ePDG lines. CommCenter logs the switch; on the cable the log may also drop
    # instead, which counts the same. Without either: full time.
    slots = {r['Slot'] for r in rows if r.get('Slot') in ('kOne', 'kTwo')}
    seen = {}
    armed = False
    def on_entry(e, msg):
        nonlocal armed
        collect(e, msg)
        if m := AIRPLANE_CHANGE.search(msg):
            armed = m.group(1) == 'false'; seen.clear()
        elif armed and (slot := log_slot(e)) in slots and DIAG_PATTERNS['ims_reg'].search(msg):
            seen.setdefault(slot, time.monotonic())
    def reset():
        nonlocal armed
        armed = True; seen.clear()
        collect.reset()
    on_entry.reset = reset
    def done():
        return bool(slots) and slots <= seen.keys() and time.monotonic() - max(seen.values()) >= DIAG_SETTLE_SECONDS
    return on_entry, done


def full_log_hint(out):
    return (f'Full CommCenter log for this interval, including IKE to ePDG (numbers masked): {out / "commcenter.log"}')


async def run_diagnose(device, args, rows):
    out = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'diagnose')
    out.mkdir(parents=True, mode=0o700)
    pressed = enter_pressed()
    print(f'Collecting CommCenter logs for up to {args.seconds} s. To make iOS register again,\n'
          'enable airplane mode, wait 10 seconds, then disable it (leave Wi-Fi enabled).\n'
          'Collection stops when the SIMs register again' + ('; Enter stops early.' if pressed else '.'),
          flush=True)
    state = {}
    on_entry, done = registered_again(state, rows, diag_collect(state))
    await commcenter_stream(device, args.seconds, out / 'commcenter.log', on_entry,
                            stop=lambda: done() or bool(pressed and pressed()))
    report = diag_report(state, rows)
    print(report, flush=True)
    (out / 'report.txt').write_text(report + '\n', encoding='utf-8')
    print('\nSIM slots are identified heuristically from the log. ' + full_log_hint(out), flush=True)
    return 0


async def run_watch_call(device, args, rows):
    out = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'watch-call')
    out.mkdir(parents=True, mode=0o700)
    pressed = enter_pressed()
    print(f'Listening to CommCenter for up to {args.seconds} s. Make a test call now.\n'
          'For VoWiFi: airplane mode + Wi-Fi. For VoLTE: Wi-Fi off.' +
          ('\nPress Enter after the call to finish early.' if pressed else ''), flush=True)
    state = {}
    collect = diag_collect(state)
    def on_entry(e, msg):
        sip = collect(e, msg)
        slot = {'kOne':'SIM 1','kTwo':'SIM 2'}.get(log_slot(e), '     ')
        if sip:
            first, access, codec = sip
            extra = ' · '.join(x for x in (access, codec and 'codec ' + CODEC_NAMES.get(codec, codec)) if x)
            print(f'  {e.timestamp:%H:%M:%S} {slot} SIP {first}' + (f'  [{extra}]' if extra else ''), flush=True)
        elif m := DIAG_PATTERNS['call_status'].search(msg):
            print(f'  {e.timestamp:%H:%M:%S} {slot} call: {m.group(1)}', flush=True)
    on_entry.reset = collect.reset
    await commcenter_stream(device, args.seconds, out / 'commcenter.log', on_entry, stop=pressed)
    codecs = sorted({CODEC_NAMES.get(v['codec'][0], v['codec'][0]) for v in state.values() if 'codec' in v})
    print('\nNegotiated codecs: ' + (', '.join(codecs) if codecs else 'no calls with an SDP answer'), flush=True)
    report = diag_report(state, rows)
    print(report, flush=True)
    (out / 'report.txt').write_text(report + '\n', encoding='utf-8')
    print('\n' + full_log_hint(out), flush=True)
    return 0


# What only the user can check by hand; the log shows registration, not whether a call really works.
REPORT_QUESTIONS = (
    'VoWiFi: calls work over Wi-Fi in airplane mode',
    'VoWiFi: activates automatically without airplane mode',
    'VoLTE: without Wi-Fi, calls stay on 4G/5G',
    '5G: n band in *3001#12345#* (for example n1; n means no NR bands)',
    'iMessage/FaceTime work with the phone number',
    'SMS over Wi-Fi in airplane mode',
    'Personal Hotspot',
    'Call merging and holding',
)


def report_answer(text):
    text = mask_log(text.strip())[:60]
    if not text: return 'not tested'
    low = text.lower()
    if low in ('y', 'yes', '+'): return 'yes'
    if low in ('n', 'no', '-'): return 'no'
    return text


def report_text(info, rows, diag, answers, region=''):
    # Shareable: model, iOS, operator, the bundle iOS loaded, masked log summary and the user's answers.
    lines = [f'CarrierSIM {VERSION} · profile report',
             f"{MODELS.get(info.get('ProductType'), {}).get('name', info.get('ProductType'))} · "
             f"iOS {info.get('ProductVersion')} ({info.get('BuildVersion')})" + (f' · {region}' if region else '')]
    for r in rows:
        if r.get('Slot') not in SLOT_NAMES: continue
        plmn = f"{r.get('MCC', '')}-{r.get('MNC', '')}"
        bundle = str(r.get('CFBundleIdentifier', '')).removeprefix('com.apple.') or 'unknown'
        lines.append(f"{SLOT_NAMES[r['Slot']]} · {OPERATORS.get(plmn, plmn)} ({plmn}) · profile {bundle} "
                     f"{r.get('CFBundleVersion', '')}".rstrip())
        for question, answer in zip(REPORT_QUESTIONS, (answers or {}).get(r['Slot'], ())):
            lines.append(f'    {question.split(" (")[0]}: {answer}')
    if answers is None:
        lines.append('Manual checks: not provided (non-interactive run)')
    lines.append('CommCenter log:' + (diag or '\n  no data'))
    return '\n'.join(lines)


@contextlib.contextmanager
def plain_ctrl_c():
    # asyncio.run turns the first Ctrl-C into a cancel of the main task, which a blocking input()
    # never sees: the question keeps waiting and the cancel fires later, after the report is done.
    try: previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError: previous = None  # not the main thread
    try: yield
    finally:
        if previous is not None: signal.signal(signal.SIGINT, previous)


async def run_report(device, args, rows):
    out = args.runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'report')
    out.mkdir(parents=True, mode=0o700)
    print(f'Collecting CommCenter logs for {args.seconds} s. Enable airplane mode, wait 10 seconds, then disable it '
          '(leave Wi-Fi enabled).', flush=True)
    state = {}
    await commcenter_stream(device, args.seconds, out / 'commcenter.log', diag_collect(state))
    answers = {}; region = ''; interrupted = False
    if sys.stdin.isatty():
        print('\nWhat did you test yourself? y — yes, n — no, Enter — not tested.', flush=True)
        try:
            with plain_ctrl_c():
                for r in rows:
                    if r.get('Slot') not in SLOT_NAMES: continue
                    print('\n  ' + sim_header(r), flush=True)
                    # Stored one by one: an interrupted SIM keeps the answers already given.
                    given = answers[r['Slot']] = []
                    for q in REPORT_QUESTIONS: given.append(report_answer(input(f'  {q}: ')))
                # Region is free text, not a yes/no answer.
                region = mask_log(input('\n  City or region (optional; Enter skips): ').strip())[:60]
        except (EOFError, KeyboardInterrupt):
            # The log is already collected: finish the report with the answers given so far.
            interrupted = True
            print('\nQuestions interrupted; report includes the answers already given.', flush=True)
    else:
        answers = None  # not asked at all, unlike a phone without SIM slots
        print('\nManual-check questions skipped: non-interactive run. '
              'To answer, choose menu item 11.', flush=True)
    text = report_text(DIAG.get('info', {}), rows, diag_report(state, rows), answers, region)
    if interrupted: text += '\nManual checks: questions interrupted; answers incomplete'
    (out / 'report.txt').write_text(text + '\n', encoding='utf-8')
    print('\n===== Profile report\n' + text +
          '\n===== End of report\n'
          f'Phone number, IMSI and ICCID are excluded from the report. File: {out / "report.txt"}', flush=True)
    return 0


async def diagnostics(args):
    device=await ready_device(args.udid,args.wait_seconds)
    try:
        info=await device_info(device); DIAG['info']=info; configure_device(info)
        rows=await carrier_rows(device)
        require(not no_cellular_sim(info, rows), no_cellular_sim(info, rows))
        print(f"\n  {MODELS.get(info['ProductType'], {}).get('name', info['ProductType'])} · iOS {info['ProductVersion']} ({info['BuildVersion']})",flush=True)
        for r in rows:
            if r.get('Slot') in SLOT_NAMES: print('  '+sim_header(r),flush=True)
        print(flush=True)
        return await (run_diagnose if args.diagnose else run_report if args.report else run_watch_call)(device,args,rows)
    finally:await device.close()


async def execute(args,assets):
    udid=args.udid
    device=await ready_device(udid,args.wait_seconds)
    run=None
    try:
        info=await device_info(device); DIAG['info']=info; configure_device(info)
        require(info['ActivationState']=='Activated','iPhone is not activated.')
        rows=await carrier_rows(device)
        top=await device.get_value() or {}
        unavailable = no_cellular_sim({**info, **top}, rows)
        if unavailable and not args.recover:
            output_section('DEVICE')
            print(f"  {MODELS.get(info['ProductType'], {}).get('name', info['ProductType'])} · iPadOS {info['ProductVersion']} ({info['BuildVersion']})", flush=True)
            if args.status:
                print('\n  '+unavailable, flush=True)
                return NOTHING_TO_WRITE if os.environ.get('CARRIERSIM_PLAN') else 0
            raise RuntimeError(unavailable)
        slots=SLOT_CHOICES[args.sims]
        # A slot picked by number is the user's explicit choice; "all" leaves foreign SIMs alone.
        any_mcc=args.sims!='all'; skipped=[]
        sims=(select_sims(rows,args.bundles,slots,any_mcc,skipped,only_skipped_ok=args.status)
              if not (args.restore or args.restore_backup or args.recover) else [])
        restore_imsis=None
        if args.restore:
            sims=[{'slot':r['Slot'],'plmn':str(r.get('MCC',''))+str(r.get('MNC','')),'bundle':None}
                  for r in rows if r.get('Slot') in slots]
            if args.sims!='all':
                # Only this SIM's alias is removed, so its IMSI must be known.
                restore_imsis={s['imsi'] for s in select_sims(rows,None,slots)}
        output_section('DEVICE AND PLAN')
        print(f"  {MODELS.get(info['ProductType'], {}).get('name', info['ProductType'])} · iOS {info['ProductVersion']} ({info['BuildVersion']})",flush=True)
        row_by_slot={r.get('Slot'):r for r in rows}
        for s in sims:
            target='stock profile' if args.restore else s['bundle'].removesuffix('.bundle')+' (by IMSI)'
            print_sim_plan(row_by_slot[s['slot']], top, target)
        for slot in skipped:
            print_sim_plan(row_by_slot[slot], top, 'unchanged (foreign SIM: add its MCCMNC '
                           'to bundle.yaml or choose it in menu item 7)')
        print(flush=True)
        bundles=sorted({s['bundle'] for s in sims if s['bundle']})
        if bundles and DEVICE_FAMILY == 'iPhone':
            # Blocking I/O off the event loop; cached per process, so retries do not wait again.
            catalog=await asyncio.to_thread(load_catalog,args.runs)
            if catalog: output_section('PROFILE INFORMATION')
            for name in bundles:
                error=catalog_name_error(name,catalog);require(not error,error)
                if catalog: print(flush=True)
                for line in passport_lines(name,catalog): print(line,flush=True)
            if catalog: print(flush=True)
        # Only a warning: the write itself stops on the refusal seen in the iPhone log (AirliftClosed).
        if (airlift_may_be_closed(info) and not args.recover
                and (bool(sims) or args.restore or args.restore_backup)):
            print(f"\n  Warning: writing on iOS {info['ProductVersion']} ({info['BuildVersion']}) may fail: "
                  'from 27.2 beta 3 the iPhone refuses AirTraffic file moves. The normal installer will try; '
                  'if the iPhone refuses, it stops before changes and reports this. '
                  'The installed profile remains.\n',flush=True)
        if args.status:
            print('  Current is the profile the device is using.\n'
                  '  Plan is the profile proposed for installation.\n\n'
                  '  Identify the SIM by the last four ICCID digits:\n'
                  '  Settings → General → About → ICCID.',flush=True)
            blocked=pending(args.runs,udid)
            if blocked:
                # Menu 7 confirms after this plan: explain a blocked write before asking for confirmation.
                print('Warning: a previous operation on this iPhone is incomplete; writing will not start. First '
                      +recover_hint()+'.',flush=True)
            # The menu must not ask "write?" when the write would not start or has nothing to write.
            if os.environ.get('CARRIERSIM_PLAN') and (blocked or not sims): return NOTHING_TO_WRITE
            return
        custom=None
        if args.trigger:
            # Checked before any run folder or phone change; the copy below must be this same file.
            custom=check_trigger(args.trigger,{str(r.get('MCC',''))+str(r.get('MNC','')) for r in rows},{s['bundle'] for s in sims if s['bundle']})
            check_trigger_hardware(args.trigger,info['HardwareModel'])
        unresolved=pending(args.runs,udid)
        # Recomputed on every attempt: a failed attempt's auto-recovery may already have fixed some stages.
        recover=args.recover
        if recover == Path('AUTO'):
            if not unresolved:
                print('No unfinished operations for this iPhone; nothing to recover.');return 0
            recover=unresolved
        require(not unresolved or recover,
                'A previous operation on this iPhone is incomplete. First '+recover_hint()+
                ', then try again. Stage: '+str(unresolved[0] if unresolved else ''))
        run=args.runs/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6])
        run.mkdir(mode=0o700); DIAG['run']=run; save_environment(run)
        output_section('EXECUTION')
        print('  Backups and log:\n    '+str(run),flush=True)
        print('\n  Keep the iPhone connected until the operation finishes.',flush=True)
        if warning := backup_warning(info.get('cloud_backup')): print(warning,flush=True)
        save_json(run/'device.json',{**info,'udid_hash':digest(udid.encode())})
        trigger=None
        plmns={str(r.get('MCC',''))+str(r.get('MNC','')) for r in rows}
        # Prefer a trigger with signed overrides for this board; otherwise the first one that fits the SIMs.
        fitting=[]
        names = ('AVEA_tr_iPad.ipcc',) if DEVICE_FAMILY == 'iPad' else ('AVEA_tr.ipcc','Swisscom_ch.ipcc','O2_Germany.ipcc')
        for name in (() if args.trigger or assets is None else names):
            candidate=run/name;candidate.write_bytes(assets['triggers/'+name][1])
            try:
                check_trigger(candidate,plmns,{s['bundle'] for s in sims if s['bundle']})
            except RuntimeError:candidate.unlink();continue
            if check_trigger_hardware(candidate,info['HardwareModel'],warn=False):
                trigger=candidate;break
            fitting.append(candidate)
        if trigger is None and fitting:
            trigger=fitting[0];check_trigger_hardware(trigger,info['HardwareModel'])
        for extra in fitting:
            if extra!=trigger:extra.unlink()
        if args.trigger:
            trigger=run/'custom-trigger.ipcc';trigger.write_bytes(args.trigger.read_bytes())
            require(digest(trigger.read_bytes())==custom['sha256'],'IPCC file changed after validation.')
        # Recovery itself needs no trigger: it is only for the rescan at the end.
        require(trigger is not None or recover,'No independent trigger found for these SIMs.')
        if trigger is not None: DIAG['trigger']=trigger.name
        if not args.recover:
            # No unfinished stage is known here (checked above), so AirLift leftovers are stale.
            cleaned=await clean_phone(device,run)
            kept=[n for n,v in cleaned.items() if str(v).startswith('not removed')]
            removed=[n for n in cleaned if n not in kept]
            if removed:print('\n  Removed leftovers from previous runs:\n    '+'\n    '.join(removed),flush=True)
            if kept:print('\n  Could not remove leftovers from previous runs:\n    '+'\n    '.join(kept),flush=True)
        if recover:
            if isinstance(recover,list):await recover_all(device,recover,run)
            else:await recover_stage(device,recover.resolve(),run)
        elif args.restore:
            print('\n  [1/4] Preparing rescan…',flush=True)
            init=run/'initialize';init.mkdir();await install_trigger(device,trigger,init)
            print('\n  [2/4] Saving current settings…',flush=True)
            original=await transfer(device,run/'snapshot')
            if restore_imsis is not None:
                now={s['imsi'] for s in select_sims(await carrier_rows(device),None,slots)}
                require(now==restore_imsis,'SIMs changed during the operation; write cancelled.')
            desired=remove_imsi_links(original,restore_imsis)
            removed=len(original)-len(desired)
            save_json(run/'plan.json',{'action':'remove-imsi','sims':args.sims,'removed':removed,
                                      'before':tree_hash(original),'after':tree_hash(desired)})
            if not removed:
                print('\n  [3/4] No IMSI aliases for the selected SIMs: they already use stock selection. Nothing changed.',flush=True)
                return 0
            print(f'\n  [3/4] Removing {removed} IMSI aliases. Verifying…',flush=True)
            await transfer(device,run/'restore',payload=desired,expected=original)
            require(await transfer(device,run/'readback')==desired,'Readback mismatch.')
        elif args.restore_backup:
            failed=args.restore_backup.resolve()/'snapshot'
            record=read_json(failed/'journal.json');bound(record,device)
            desired=read_tree_zip(failed/'original.zip')
            require(tree_hash(desired)==record.get('original_hash'),'Backup is damaged.')
            await transfer(device,run/'restore',payload=desired,recovery=True)
            require(await transfer(device,run/'readback')==desired,'Readback mismatch.')
        else:
            # A non-overlapping trigger also creates the user catalog on a clean phone.
            init=run/'initialize';init.mkdir()
            print('\n  [1/4] Preparing rescan…',flush=True)
            await install_trigger(device,trigger,init)
            print('\n  [2/4] Saving baseline settings…',flush=True)
            original=await transfer(device,run/'snapshot')
            require(original is not None,'Could not save the original catalog.')
            current=select_sims(await carrier_rows(device),args.bundles,slots,any_mcc)
            require(current==sims,'SIMs changed during the operation; write cancelled.')
            desired=make_plan(original,sims)
            save_json(run/'plan.json',{'slots':[{k:v for k,v in s.items() if k!='imsi'} for s in sims],
                                      'before':tree_hash(original),'after':tree_hash(desired)})
            print('\n  [3/4] Writing IMSI aliases and verifying…',flush=True)
            await transfer(device,run/'apply',payload=desired,expected=original)
            require(await transfer(device,run/'readback')==desired,'Readback mismatch.')
        if trigger is None:
            print('Catalog restored. Rescan skipped: no suitable trigger '
                  'or assets.zip unavailable. If service has not returned, restart the iPhone.',flush=True)
            return 0
        print('\n  [4/4] Waiting for profile selection and signature verification…',flush=True)
        rescan=run/'rescan';rescan.mkdir()
        installation=await install_trigger(device,trigger,rescan)
        result=report_log(rescan/'commcenter.log',sims)
        save_json(run/'result.json',{'catalog_verified':True,'installation':installation,'slots':result})
        output_section('RESULT')
        unconfirmed=False
        for s in result:
            ok=s['verified'] and (args.restore or (s['selected'] or '').lower()==s['expected'].lower());unconfirmed |= not ok
            print(f"  {SLOT_NAMES[s['slot']]} ({s['plmn']}): "+slot_outcome(s,ok),flush=True)
        if args.restore:
            print(('Selected SIM\'s IMSI alias removed; the other SIM is unchanged.' if restore_imsis else
                   'All IMSI aliases removed.')+' Normal carrier links preserved.',flush=True)
        installing=not (args.restore or args.restore_backup or args.recover)
        missing=[]
        if installing:
            # Roll back only on evidence: iOS chose another bundle, or CommCenter logged its bundle
            # choice but not ours. A missing or broken log proves nothing and stays "unconfirmed".
            log=rescan/'commcenter.log'
            log_ok=(not (installation or {}).get('log_error') and log.exists()
                    and BUNDLE_BLOCK in log.read_text(encoding='utf-8',errors='replace'))
            missing=sorted({s['expected'] for s in result if s['expected']
                            and (s['selected'] or log_ok)
                            and (s['selected'] or '').lower()!=s['expected'].lower()})
        if missing:
            # AFC cannot read /System, so a missing bundle only shows up in the rescan log.
            # Never leave links to it: put back the catalog saved before this write.
            print('iOS did not select '+', '.join(missing)+': the bundle may not exist on this iOS build, its name may '
                  'be incorrect, or iOS selects another bundle for this SIM. Restoring previous settings…',flush=True)
            # The rescan trigger may have touched other parts of the catalog since the readback;
            # only this run's IMSI links must still be exactly as written.
            await transfer(device,run/'rollback',payload=original,
                           expected=lambda tree:imsi_links(tree)==imsi_links(desired))
            require(await transfer(device,run/'rollback-readback')==original,
                    'Previous settings were not restored: '+recover_hint()+'.')
            rescan=run/'rescan-rollback';rescan.mkdir()
            await install_trigger(device,trigger,rescan)
            save_json(run/'result.json',{'catalog_verified':True,'installation':installation,'slots':result,
                                         'rolled_back':True})
            print('Previous settings restored. Check the bundle name (bundle.yaml or menu item 7) and try again.',flush=True)
            return UNCONFIRMED
        if unconfirmed:
            print(('Catalog written, but selection was not confirmed in the log. Enable airplane mode for 15 seconds, then open '
                   'menu item 2 (--status): current should show the requested profile. If it still shows the old one, '
                   'restore stock IMSI selection with menu item 4 and send the operation log to the author.') if installing else
                  ('The log did not confirm stock selection. Enable airplane mode for 15 seconds, then open '
                   'menu item 2 (--status): current should show the carrier profile.'),flush=True)
            return UNCONFIRMED
        print('\n  Books: original sync files restored'+books_summary(run)+'.',flush=True)
        print('\n  Done. Restarting the device is recommended for full profile functionality.\n'
              '  After restarting, test service, calls and data.',flush=True)
        return 0
    except BaseException as error:
        if run:
            save_json(run/'error.json',{'error':error_line(error)})
            # execute_with_retry rolls back any unfinished stage and reports the outcome.
            print('Operation stopped. Log:',run,file=sys.stderr)
        raise
    finally:await device.close()

# ---- Diagnostics printed on failure: enough to debug without sending the runs folder.
# Never includes IMSI, UDID or serial numbers.
DIAG = {}


def win_file_version(path):
    try:
        v = C.windll.version
        size = v.GetFileVersionInfoSizeW(str(path), None)
        if not size: return None
        buf = C.create_string_buffer(size)
        if not v.GetFileVersionInfoW(str(path), 0, size, buf): return None
        ptr, length = C.c_void_p(), C.c_uint()
        if not v.VerQueryValueW(buf, '\\', C.byref(ptr), C.byref(length)): return None
        info = C.cast(ptr, C.POINTER(C.c_uint32 * 13)).contents
        ms, ls = info[2], info[3]
        return f'{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}'
    except Exception:
        return None


def sysctl(name):
    try:
        return subprocess.run(['/usr/sbin/sysctl', '-n', name], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def linux_usbmuxd_status():
    """Find the daemon without requiring sbin in the user's PATH."""
    import shutil
    executable = shutil.which('usbmuxd')
    if not executable:
        executable = next((str(path) for path in (Path('/usr/sbin/usbmuxd'), Path('/sbin/usbmuxd'),
                                                 Path('/usr/bin/usbmuxd'), Path('/bin/usbmuxd'))
                           if path.is_file() and os.access(path, os.X_OK)), None)
    try:
        socket_exists = stat.S_ISSOCK(Path('/run/usbmuxd').stat().st_mode)
    except OSError:
        socket_exists = False
    if executable:
        return f'installed ({executable}); socket {"available" if socket_exists else "not detected"}'
    if socket_exists:
        return '/run/usbmuxd socket exists; executable not found'
    return 'not found'


def linux_platform_description():
    import platform
    try:
        distro = platform.freedesktop_os_release().get('PRETTY_NAME', '').strip()
    except OSError:
        distro = ''
    details = f'{platform.platform()} · {platform.machine()}'
    return f'{distro} · {details}' if distro else details


def environment_info():
    import platform
    from importlib.metadata import version, metadata, PackageNotFoundError
    rows = [('CarrierSIM', VERSION),
            ('Script build', digest(SELF.read_bytes())[:12]),
            ('Python', f"{sys.version.split()[0]} {platform.machine()} {'64' if sys.maxsize > 2**32 else '32'}-bit")]
    libs = []
    for name in ('pymobiledevice3', 'cryptography', 'pyimg4', 'pylzss', 'lzfse'):
        try:
            placeholder = 'placeholder' in (metadata(name).get('Summary') or '')
            libs.append(f"{name} {version(name)}{' (placeholder)' if placeholder else ''}")
        except PackageNotFoundError:
            libs.append(f'{name} no')
    rows.append(('Libraries', ', '.join(libs)))
    if sys.platform == 'darwin':
        cpu = 'Apple Silicon' if sysctl('hw.optional.arm64') == '1' else 'Intel'
        if sysctl('sysctl.proc_translated') == '1': cpu += ', Python under Rosetta'
        rows.append(('macOS', f"{platform.mac_ver()[0]} · {sysctl('hw.model') or '?'} · {cpu} · {sysctl('machdep.cpu.brand_string') or ''}".rstrip(' ·')))
        try:
            at = plistlib.loads(Path('/System/Library/PrivateFrameworks/AirTrafficHost.framework/Resources/Info.plist').read_bytes())
            rows.append(('AirTrafficHost', f"{at.get('CFBundleShortVersionString')} ({at.get('CFBundleVersion')})"))
        except Exception:
            rows.append(('AirTrafficHost', 'version unavailable'))
    elif sys.platform == 'win32':
        w = sys.getwindowsversion()
        rows.append(('Windows', f"{platform.release()} {platform.version()} (build {w.build}) · {platform.machine()}"))
        dirs = [Path(d) for d in APPLE_DIRS]
        for key in ('CommonProgramW6432', 'CommonProgramFiles'):
            if os.environ.get(key):
                dirs += [Path(os.environ[key])/'Apple'/'Mobile Device Support', Path(os.environ[key])/'Apple'/'Apple Application Support']
        found = {}
        for d in dict.fromkeys(dirs):
            for name in ('AirTrafficHost.dll', 'MobileDevice.dll', 'CoreFoundation.dll'):
                if name not in found and (d/name).is_file():
                    found[name] = f'{win_file_version(d/name) or "?"} ({d})'
        for name in ('AirTrafficHost.dll', 'MobileDevice.dll', 'CoreFoundation.dll'):
            rows.append((name, found.get(name, 'not found')))
        itunes = [Path(os.environ[k])/'iTunes'/'iTunes.exe' for k in ('ProgramW6432', 'ProgramFiles') if os.environ.get(k)]
        itunes = next((x for x in itunes if x.is_file()), None)
        rows.append(('iTunes', win_file_version(itunes) if itunes else 'iTunes.exe not found (possibly a Microsoft Store version)'))
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service'):
                rows.append(('Apple Mobile Device Service', 'installed'))
        except Exception:
            rows.append(('Apple Mobile Device Service', 'not found'))
    elif sys.platform.startswith('linux'):
        rows.append(('Linux', linux_platform_description()))
        rows.append(('AirTraffic', 'native com.apple.atc via pymobiledevice3'))
        rows.append(('usbmuxd', linux_usbmuxd_status()))
        rows.append(('Transport', 'usbmuxd / lockdownd'))
    else:
        rows.append(('OS', platform.platform()))
    return rows



def outstanding_assets(tree):
    # Read Books/Sync/Database/OutstandingAssets_4.sqlite (with its WAL) from a Books backup.
    import sqlite3
    base = OUTSTANDING_DB.removeprefix('Books/')
    if base not in tree: return None
    try:
        return read_outstanding({suffix: tree[base + suffix][1] for suffix in ('', '-wal', '-shm') if base + suffix in tree})
    except sqlite3.Error:
        return None


def books_summary(run):
    changes = sorted({c for j in run.glob('*/journal.json') for c in read_json(j).get('books_other_changes', [])})
    return ('; Books root changes during installation: ' + ', '.join(changes[:10])) if changes else ''


def run_details(run):
    rows = []
    for journal in sorted(run.glob('*/journal.json'), key=lambda p: p.stat().st_mtime):
        stage = journal.parent
        try: j = read_json(journal)
        except Exception: continue
        line = f"phase {j.get('phase')}, complete {bool(j.get('complete'))}, Books restored {j.get('books_restored')}"
        if j.get('stale_books_removed'): line += f", stale entries removed: {j['stale_books_removed']}"
        if j.get('books_other_changes'): line += f", iOS changed in Books: {', '.join(j['books_other_changes'][:8])}"
        if j.get('media_leftovers'):
            old = sorted({n.rsplit('-', 1)[-1] for n in j['media_leftovers']} - {str(j.get('source', '')).rsplit('-', 1)[-1]})
            rows.append(('  Previous run leftovers in Media', ', '.join(j['media_leftovers'][:12]) + (f' (runs: {len(old)})' if old else '')))
        if j.get('operation_error'): line += f", error: {j['operation_error']}"
        rows.append((f'Stage {stage.name}', line))
        try:
            b = read_json(stage/'books.json'); tree = read_tree_zip(stage/'books.zip')
            known = [n for n in (x.removeprefix('Books/') for x in BOOK_FILES + BOOK_DIRS[1:]) if n in tree]
            rows.append(('  Books before operation', f"{'existed' if b.get('existed') else 'absent'}, sync files: {', '.join(known) or 'no'}"
                         + (f", root: {', '.join(b['top'][:15])}" if b.get('top') else '')))
            outstanding = outstanding_assets(tree)
            if outstanding is not None:
                mine = [x for x in outstanding if our_trace(x[0])]
                rows.append(('  Outstanding Books downloads', f'{len(outstanding)}, belonging to this tool {len(mine)}'))
                for pid, done in mine[:6]:
                    rows.append(('    download', f'{pid} → {done or "incomplete"}'))
            traces = sorted(n for n, (k, d) in tree.items() if k == 'f' and b'airlift' in d)
            if traces: rows.append(('  airlift traces in Books', ', '.join(traces)))
        except Exception:
            pass
        host = stage/'host.jsonl'
        if host.exists():
            names = []
            for raw in host.read_text(encoding='utf-8', errors='replace').splitlines():
                if not raw.startswith('CARRIER_SWAP_JSON:'): continue
                try: row = json.loads(raw.split(':', 1)[1])
                except ValueError: continue
                if row.get('event') == 'manifest':
                    if type(row.get('expected')) is int:
                        rows.append(('  AirTraffic response',
                                     f"expected {row['expected']}, matched {row.get('matched')}, "
                                     f"duplicates {row.get('duplicate', 0)}"))
                        continue
                    book = row.get('book', [])
                    expected = set(row.get('expected', []))
                    rows.append(('  AirTraffic response', f"dataclasses {row.get('dataclasses')}, Book objects {len(book)}, "
                                 f"IsDownload {sum(1 for x in book if x.get('IsDownload') in ('True', '1'))}, "
                                 f"ours {sum(1 for x in book if x.get('AssetID') in expected)} of {len(expected)}"))
                    for x in book[:5]:
                        rows.append(('    Book', ', '.join(f'{k}={v[:60]}' for k, v in x.items())))
                elif row.get('ok') is False:
                    rows.append(('  AirTraffic error', str(row.get('error'))))
                elif row.get('event') == 'message':
                    names.append(row.get('name'))
            if names: rows.append(('  AirTraffic messages', ' → '.join(map(str, names))))
        dlog = stage/'device.log'
        if dlog.exists():
            lines = dlog.read_text(encoding='utf-8', errors='replace').splitlines()
            # Only AirTraffic, Books and sandbox problems; trustd/wifid/atc(Apps) noise is in device.log.
            source = re.compile(r'\batc\((AirTraffic\w*|ATFoundation|Books|Foundation)\)|kernel\(Sandbox\)')
            problem = re.compile(r'<Error>|<Fault>|\bdeny\(|Aborting|SyncFailed|ErrorCode|installOnly=1|'
                                 r'could not|not found|no such file', re.I)
            # Present in every successful run as well: not a cause.
            benign = ('ATGetUsageForPath', 'Artwork file does not exist', 'ATStoreInfo with no',
                      "Asset path isn't in one of the expected directories", 'Could not create sandbox extension')
            key = [l for l in lines if source.search(l) and problem.search(l) and not any(b in l for b in benign)]
            rows.append(('  iPhone log', f'{len(lines)} lines, relevant {len(key)}'))
            for l in key[-12:]:
                rows.append(('    iPhone', re.sub(r'^\w{3} +\d+ [\d:]+ \S+ ', '', l.strip())[:300]))
        err = stage/'host.stderr'
        if err.exists():
            tail = [l.strip()[:200] for l in err.read_text(encoding='utf-8', errors='replace').splitlines() if l.strip()][-5:]
            for l in tail: rows.append(('  host.stderr', l))
    for name in ('initialize', 'rescan'):
        f = run/name/'installation.json'
        if f.exists():
            try:
                j = read_json(f)
                rows.append((f'Trigger ({name})', f"installed {j.get('ipcc_installation_completed')}"
                             + (f", error: {j['installation_error']}" if j.get('installation_error') else '')
                             + (f", log: {j['log_error']}" if j.get('log_error') else '')))
            except Exception:
                pass
    return rows



class Tee:
    # Mirrors the console into the session log so a runs folder carries everything shown.
    def __init__(self, stream, log):
        self.stream, self.log = stream, log
    def write(self, data):
        self.stream.write(data)
        with contextlib.suppress(Exception): self.log.write(data); self.log.flush()
        return len(data)
    def flush(self):
        self.stream.flush()
    def __getattr__(self, name):
        return getattr(self.stream, name)


def start_session_log(runs):
    path = runs / (datetime.now().strftime('%Y%m%d-%H%M%S-') + 'session.log')
    log = path.open('a', encoding='utf-8', buffering=1)
    log.write(f'CarrierSIM {VERSION} · build {digest(SELF.read_bytes())[:12]}\n')
    log.write(' '.join(['carrier.py'] + sys.argv[1:]) + '\n')
    sys.stdout, sys.stderr = Tee(sys.stdout, log), Tee(sys.stderr, log)
    DIAG['session_log'] = str(path)


def save_environment(run):
    with contextlib.suppress(Exception):
        save_json(run / 'environment.json', dict(environment_info()))


def print_diagnostics(error):
    rows = []
    try: rows += environment_info()
    except Exception as e: rows.append(('Environment', f'unavailable: {error_text(e)}'))
    info = DIAG.get('info')
    if info:
        rows.append(('iPad' if str(info['ProductType']).startswith('iPad') else 'iPhone', f"{MODELS.get(info['ProductType'], {}).get('name', '?')} · {info['ProductType']} · "
                     f"{info['HardwareModel']} · iOS {info['ProductVersion']} ({info['BuildVersion']}) · {info['ActivationState']}"))
        for c in info.get('carriers', []):
            rows.append(('  SIM', f"{c.get('Slot')} {c.get('MCC','')}{c.get('MNC','')} {c.get('CFBundleIdentifier','')} {c.get('CFBundleVersion','')}"))
    args = DIAG.get('args')
    if args is not None:
        rows.append(('Action', ' '.join(a for a in sys.argv[1:]) or 'install'))
        rows.append(('Profile', f"{getattr(args, 'bundles', None) or getattr(args, 'bundle', None)}, SIM: {getattr(args, 'sims', 'all')}"))
    if DIAG.get('trigger'): rows.append(('Trigger', DIAG['trigger']))
    if DIAG.get('cleanup'): rows.append(('Device cleanup', json.dumps(DIAG['cleanup'], ensure_ascii=False)[:600]))
    run = DIAG.get('run')
    if run:
        rows.append(('Operation directory', str(run)))
        try: rows += run_details(run)
        except Exception as e: rows.append(('Logs', f'unreadable: {error_text(e)}'))
    rows.append(('Error', error_line(error)))
    import traceback
    frames = [f for f in traceback.extract_tb(error.__traceback__) if f.filename.endswith(('carrier.py', 'launch.py', 'airtraffic_apple.py'))]
    if frames:
        rows.append(('Location', ' → '.join(f'{f.name}:{f.lineno}' for f in frames[-4:])))
    if DIAG.get('session_log'): rows.append(('Session log', DIAG['session_log']))
    text = '\n'.join(f'{k}: {v}' for k, v in rows)
    print('\n===== Debug information =====', file=sys.stderr)
    print(text, file=sys.stderr)
    print('===== End of debug block =====\n', file=sys.stderr, flush=True)
    if run:
        with contextlib.suppress(Exception):
            (run / 'diagnostics.txt').write_text(text + '\n\n' + ''.join(traceback.format_exception(error)), encoding='utf-8')



def main():
    if len(sys.argv)>1 and sys.argv[1]=='--_host':
        try:
            value=json.loads(sys.stdin.readline()) if sys.argv[2]=='check' else read_json(Path(sys.argv[2]))
            host_worker(value)
            return 0
        except Exception as e:framed({'ok':False,'error':error_text(e)});return 1
    parser=argparse.ArgumentParser(description='Install T-Mobile Wholesale automatically with --tmobile-wholesale. '
        'Default IMSI workflow: bundle.yaml for Russian and Belarusian SIMs; foreign SIMs require explicit selection. '
        'No normal-install model or iOS restriction; compatibility is not guaranteed.',
        add_help=False)
    parser.add_argument('--version', action='version', version=f'CarrierSIM {VERSION}')
    parser.add_argument('-h','--help',action='help',help='show this help')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--check',action='store_true',help='check files and AirTraffic backend without connecting to a device')
    group.add_argument('--atc-probe',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--status',action='store_true',help='show detected SIMs and a read-only plan')
    group.add_argument('--restore',action='store_true',help='remove IMSI aliases and restore stock selection; --sims 1 or 2 affects only that SIM; GID aliases are unchanged')
    group.add_argument('--restore-backup',type=Path,metavar='DIRECTORY',help='fallback: restore the entire carrier catalog from this backup run')
    group.add_argument('--recover',type=Path,nargs='?',const=Path('AUTO'),metavar='STAGE',help='recover interrupted stages and GID operations; verified installed GID profiles are left applied')
    group.add_argument('--report',action='store_true',help='shareable profile report: CommCenter log and manual checks; read-only')
    group.add_argument('--diagnose',action='store_true',help='SIM report: IMS, VoLTE/VoWiFi/VoNR, roaming, network, 5G SA; log read-only')
    group.add_argument('--watch-call',action='store_true',help='listen during a test call: codec (EVS/AMR), access network; read-only')
    experiment = parser.add_mutually_exclusive_group()
    experiment.add_argument('--gid1', metavar='HEX', help='experimental PLMN/GID1 alias; requires explicit --bundle and --sims 1 or 2')
    experiment.add_argument('--undo-gid1', type=Path, metavar='RUN_DIRECTORY', help='journal-bound alias removal or interrupted Wholesale-removal recovery; --status previews')
    experiment.add_argument('--tmobile-wholesale', action='store_true',
                            help='USB-only automatic T-Mobile Wholesale install: discover the matched GID1 key and qualify by full ICCID; --status previews')
    experiment.add_argument('--remove-tmobile-wholesale', action='store_true',
                            help='USB-only Wholesale removal; all also discovers full-ICCID rules installed on another computer')
    experiment.add_argument('--cleanup-overrides', action='store_true',
                            help='USB-only cleanup of local GID overrides and discovered full-ICCID Wholesale rules, even with no SIMs')
    parser.add_argument('--gid1-iccid', action='store_true',
                        help='with --gid1, add the selected SIM’s full ICCID qualifier and verify carrier/operator selection')
    parser.add_argument('--bundle',metavar='BUNDLE',
                        help='use one system bundle for all selected SIMs instead of bundle.yaml, for example O2_Germany')
    parser.add_argument('--sims',choices=SLOT_CHOICES,default='all',
                        help='SIM slots: 1, 2 or all (default). Wholesale all installs every eligible line; '
                             'all removal also discovers phone-side Wholesale rules, while 1/2 removal uses local journals; '
                             'ordinary install skips foreign SIMs without their own bundle.yaml entry')
    parser.add_argument('--trigger',type=Path,metavar='IPCC',help='custom signed IPCC instead of the included trigger; board and SIMs are checked')
    parser.add_argument('--attempts',type=int,default=3,metavar='N',help='attempts after transient connection errors (default 3); GID apply never retries')
    parser.add_argument('--seconds',type=int,metavar='SECONDS',help='duration of --diagnose and --report (default 90) or --watch-call (default 180)')
    parser.add_argument('--wait-seconds',type=int,default=180,metavar='SECONDS',help='wait for connection and unlock (default 180 seconds)')
    parser.add_argument('--udid',metavar='ID',help='choose a device when several are connected')
    parser.add_argument('--wifi',action='store_true',help='connect over Wi-Fi instead of USB (slower); first pair and enable Show this iPhone when on Wi-Fi over USB; unavailable for GID modes')
    parser.add_argument('--apple-dir',action='append',default=[],metavar='DIRECTORY',help='Windows: Apple DLL directory; may be repeated')
    parser.add_argument('--runs',type=Path,default=ROOT/'runs',metavar='DIRECTORY',help='backup and log directory (default runs next to the script; add a custom directory inside a git repository to .git/info/exclude)')
    parser._optionals.title='Options'
    args=parser.parse_args()
    if args.gid1_iccid and args.gid1 is None:
        parser.error('--gid1-iccid requires --gid1; undo derives qualifiers from its saved journal.')
    if (args.gid1 is not None or args.undo_gid1 is not None or args.tmobile_wholesale
            or args.remove_tmobile_wholesale or args.cleanup_overrides):
        if any((args.check, args.atc_probe, args.restore, args.restore_backup, args.recover,
                args.report, args.diagnose, args.watch_call)):
            parser.error('GID and T-Mobile modes are incompatible with other write, recovery, diagnostic or check actions.')
        if args.wifi:
            parser.error('GID and T-Mobile installation/removal are USB-only; remove --wifi.')
        if args.cleanup_overrides:
            if args.bundle or args.sims != 'all':
                parser.error('--cleanup-overrides cleans all supported rules; do not supply --bundle or --sims 1/2.')
        elif args.tmobile_wholesale or args.remove_tmobile_wholesale:
            if args.bundle:
                parser.error('Automatic T-Mobile modes use a fixed saved/system bundle; do not supply --bundle.')
            if args.tmobile_wholesale:
                args.bundle = TMOBILE_WHOLESALE
        elif args.gid1 is not None:
            if not args.bundle or args.sims not in ('1', '2'):
                parser.error('--gid1 requires an explicit --bundle and one --sims slot (1 or 2).')
            args.gid1 = normalize_gid1(args.gid1)
        elif args.bundle or args.sims != 'all':
            parser.error('--undo-gid1 uses its saved binding, not --bundle or --sims.')
    elif args.status and any((args.check, args.atc_probe, args.restore, args.restore_backup, args.recover,
                              args.report, args.diagnose, args.watch_call)):
        parser.error('--status is incompatible with this action.')
    if not os.environ.get('CARRIERSIM_MENU'):
        print(f'CarrierSIM · {VERSION}',flush=True)
        print('Carrier profiles · iPhone / iPad',flush=True)
        print('VoWiFi / 5G / EVS  · Russia / Belarus',flush=True)
        print('Author: Vladimir B / vlw · vlwwwwww@gmail.com',flush=True)
        print('GitHub: https://github.com/ios-bundles/CarrierSIM',flush=True)
    DIAG['args']=args
    if args.bundle:
        args.bundle=args.bundle.strip().removesuffix('.bundle')+'.bundle'
        require(re.fullmatch(r'[A-Za-z0-9_]+\.bundle',args.bundle),
                'Bundle names may contain only ASCII letters, digits and _, for example O2_Germany.')
        args.bundles={'default':args.bundle}
    else:
        args.bundles=load_bundle_config()
    require(1 <= args.attempts <= 10, 'Attempts must be between 1 and 10.')
    require(0 <= args.wait_seconds <= 3600, 'Wait time must be between 0 and 3600 seconds.')
    args.seconds = args.seconds or (180 if args.watch_call else 90)
    require(10 <= args.seconds <= 1800, '--seconds must be between 10 and 1800.')
    os.umask(0o077)
    require(sys.version_info >= (3,11), 'Python 3.11 or later is required.')
    from importlib.metadata import version, PackageNotFoundError
    try: installed=version('pymobiledevice3')
    except PackageNotFoundError: raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from None
    require(installed=='11.12.5', 'pymobiledevice3 11.12.5 is required: python -m pip install -r requirements.txt')
    try: assets=load_assets()
    except Exception as error:
        # Recovery must not depend on the bundled triggers: they are only used for the final rescan.
        if not args.recover: raise
        print(f'Warning: assets.zip unavailable ({error_text(error)}). Normal stage recovery will skip rescan; GID undo requires a verified trigger.',flush=True)
        assets=None
    global APPLE_DIRS, CONNECTION
    CONNECTION = 'Network' if args.wifi else 'USB'
    APPLE_DIRS=[str(Path(p).resolve()) for p in args.apple_dir]
    if sys.platform.startswith('linux'):
        require(linux_usbmuxd_status() != 'not found',
                'usbmuxd not found. Install the system usbmuxd and start its service '
                '(for example, Debian/Ubuntu: sudo apt install usbmuxd), then retry --check.')
    # No shell, no compiler, no native executable bundled with the archive.
    check=subprocess.run(host_command()+['check'],input=json.dumps({'directories':APPLE_DIRS,'connection':CONNECTION}),
                         capture_output=True,text=True,encoding='utf-8',timeout=20)
    frames=[json.loads(l.split(':',1)[1]) for l in check.stdout.splitlines() if l.startswith('CARRIER_SWAP_JSON:')]
    require(check.returncode==0 and frames and frames[-1].get('ok'),
            backend_error(frames[-1].get('error') if frames else check.stderr.strip()))
    if args.check:
        # Every pymobiledevice3 module the phone steps use; a broken install or build fails here, not mid-run.
        import pymobiledevice3.lockdown, pymobiledevice3.usbmux, pymobiledevice3.services.afc, \
            pymobiledevice3.services.installation_proxy, pymobiledevice3.services.os_trace, pymobiledevice3.services.syslog
        for k,v in environment_info():print(f'{k}: {v}')
        print('Triggers intact, AirTraffic backend available; bundles will come from the device system. No device connections were made.');return 0
    if args.atc_probe:
        require(host_backend() == 'linux-native-atc', '--atc-probe is available only on Linux')
        from airtraffic_native import run_session
        async def probe():
            udid = await choose_device(args.udid, args.wait_seconds)
            paired = await ready_device(udid, args.wait_seconds)
            await paired.close()
            await run_session(udid, CONNECTION, [], framed, lambda: None, probe=True)
        asyncio.run(probe())
        return 0
    args.runs=args.runs.resolve()
    try:
        args.runs.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.runs,prefix='.write-test-'):pass
    except OSError:
        raise RuntimeError(f'Cannot save backups to: {args.runs}\n'
                           'Close this window, copy the entire CarrierSIM folder '
                           'to Downloads and launch it from there.') from None
    with contextlib.suppress(OSError):start_session_log(args.runs)
    print('Unlock the iPhone or iPad and confirm trust. Close Finder/iTunes sync.',flush=True)
    with operation_lock(args.runs):return asyncio.run(execute_with_retry(args,assets)) or 0


def cli():
    try:sys.exit(main())
    except KeyboardInterrupt:
        print('Interrupted. Keep the runs folder. If writing already started, '+recover_hint()+'.',file=sys.stderr);sys.exit(130)
    except Exception as e:
        if not (len(sys.argv)>1 and sys.argv[1]=='--_host'):
            with contextlib.suppress(Exception):print_diagnostics(e)
        print('Error:',error_text(e),file=sys.stderr);sys.exit(1)


if __name__=='__main__':
    cli()
