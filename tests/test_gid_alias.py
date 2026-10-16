"""Journal ownership, selection proof and recovery regressions; no phone connections."""
import contextlib
import copy
import io
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import zipfile
import unittest
from unittest.mock import AsyncMock, patch

import carrier


ALIAS = '310240_GID1-69'
BUNDLE = 'TMobile_Wholesale_US.bundle'
SYSTEM = '/System/Library/Carrier Bundles/iPhone/'
ROWS = [
    dict(Slot='kOne', MCC='311', MNC='480', CFBundleIdentifier='com.apple.Verizon_LTE_US',
         InternationalMobileSubscriberIdentity='311480000000001',
         IntegratedCircuitCardIdentity='8901000000000000001'),
    dict(Slot='kTwo', MCC='310', MNC='240', CFBundleIdentifier='com.apple.TMobile_MVNO_US',
         InternationalMobileSubscriberIdentity='310240000000002',
         IntegratedCircuitCardIdentity='8901000000000000002'),
]
QUALIFIED_ALIAS = ALIAS + '_ID-' + ROWS[1]['IntegratedCircuitCardIdentity']


def bundle_block(index, bundle, verification='Success', link=None, kind='Carrier'):
    return (carrier.BUNDLE_BLOCK + '\nResolved path: ' + SYSTEM + bundle + '\nLinking Path: ' +
            (link or carrier.PARENT + f'/BundleLinks/{kind}{index}Bundle.bundle') +
            '\nVerification Result: ' + verification + '\n' + carrier.BUNDLE_BLOCK + '\n')


def baseline_log(bundle='TMobile_MVNO_US.bundle', key=ALIAS, index=2):
    # This lookup/choice format is from the saved clean CommCenter baseline.
    return ('Matching request kCarrier bundle\nSystem Bundle Lookup\n'
            'Returning bundle match: Matches: [Name: ' + SYSTEM + key + ', Score: 70.60, include cookie: true]\n'
            'User Bundle Lookup\nReturning bundle match: Matches: [Name: , Score: 0.00, include cookie: true]\n'
            "No match in user's, choosing System match\n" + bundle_block(index, bundle) +
            'Matching response kCarrier bundle\n')


class Sandbox:
    """Replace only device/transport boundaries with an isolated catalog and durable stage files."""
    def __init__(self, root):
        self.root = root
        self.rows = copy.deepcopy(ROWS)
        self.catalog = {'Info.plist': ('f', b'original'),
                        '310240_GID1-54': ('l', b'keep this unrelated alias')}
        self.original = dict(self.catalog)
        self.mode = 'accept'
        self.baseline_key = ALIAS
        self.iccid = ROWS[1]['IntegratedCircuitCardIdentity']
        self.baseline_keys = {}
        self.failure_modes = {}
        self.system_bundles = {}
        self.rescans = []
        self.committed_aliases = []
        self.precommit = None
        self.trigger_error = False
        self.alias_commits = 0
        self.catalog_writes = 0
        self.transfer_failures = {}
        self.info = dict(ProductType='iPhone19,2', DeviceClass='iPhone', HardwareModel='V63AP',
                         ProductVersion='27.0.1', BuildVersion='24A446', ActivationState='Activated',
                         TelephonyCapability=True)
        self.clients = []
        self.device = self.client('sandbox-device')
        self.wrong_reconnect = False
        self.args = SimpleNamespace(udid=self.device.udid, wait_seconds=0, runs=root, trigger=None,
                                    gid1='69', gid1_iccid=False, undo_gid1=None, bundle=BUNDLE, sims='2', status=False,
                                    tmobile_wholesale=False, remove_tmobile_wholesale=False, cleanup_overrides=False,
                                    attempts=4, diagnose=False, watch_call=False, report=False,
                                    recover=None, restore=False, restore_backup=None)

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        replacements = dict(ready_device=AsyncMock(side_effect=self.ready),
                            choose_device=AsyncMock(side_effect=lambda *_: self.device.udid),
                            device_info=self.read_info, carrier_rows=self.read_rows,
                            cloud_backup=AsyncMock(return_value=None), save_environment=lambda *_: None,
                            gid_trigger=self.trigger, transfer=self.transfer, install_trigger=self.rescan,
                            check_trigger=lambda *_: {}, CONNECTION='USB')
        for name, replacement in replacements.items():
            self.stack.enter_context(patch.object(carrier, name, replacement))
        self.stack.enter_context(patch.multiple(carrier, DEVICE_FAMILY=carrier.DEVICE_FAMILY,
            TARGET=carrier.TARGET, SYSTEM_BUNDLE_DIR=carrier.SYSTEM_BUNDLE_DIR,
            SYSTEM_PREFIX=carrier.SYSTEM_PREFIX))
        self.stack.enter_context(patch.dict(carrier.DIAG, {}, clear=True))
        self.stack.enter_context(patch.dict(carrier.LOG_SECRETS, clear=True))
        # Real recover_stage reads and validates its local backup; this AFC has no remote export.
        afc = AsyncMock()
        afc.__aenter__.return_value = afc
        self.stack.enter_context(patch('pymobiledevice3.services.afc.AfcService', return_value=afc))
        self.stack.enter_context(patch.object(carrier, 'exists', AsyncMock(return_value=None)))
        self.stack.enter_context(patch.object(carrier, 'restore_books', AsyncMock(return_value=[])))
        return self

    def __exit__(self, *error):
        return self.stack.__exit__(*error)

    def client(self, udid):
        device = SimpleNamespace(udid=udid, closed=False, disconnected=False,
                                 get_value=AsyncMock(return_value=copy.deepcopy(self.info)))
        async def close():
            device.closed = True
        device.close = AsyncMock(side_effect=close)
        self.clients.append(device)
        return device

    async def ready(self, *_):
        if self.device.closed or self.device.disconnected:
            self.device = self.client('other-device' if self.wrong_reconnect else self.device.udid)
        return self.device

    def connected(self, device):
        if device.closed or device.disconnected:
            from pymobiledevice3.exceptions import ConnectionTerminatedError
            raise ConnectionTerminatedError()

    async def read_info(self, device):
        self.connected(device)
        return dict(self.info)

    async def read_rows(self, device):
        self.connected(device)
        return copy.deepcopy(self.rows)

    def row(self, iccid):
        return next(row for row in self.rows if row['IntegratedCircuitCardIdentity'] == iccid)

    def dual_mvno(self):
        self.rows[0].update(MCC='310', MNC='240', CFBundleIdentifier='com.apple.TMobile_MVNO_US',
                            InternationalMobileSubscriberIdentity='310240000000001')

    def key(self, iccid):
        return self.baseline_keys.get(iccid, self.baseline_key)

    def qualified_alias(self, iccid):
        return self.key(iccid) + '_ID-' + iccid

    def selected_bundle(self, row):
        if row['MCC'] + row['MNC'] != '310240':
            return 'Verizon_LTE_US.bundle'
        key = self.key(row['IntegratedCircuitCardIdentity'])
        for alias in (key + '_ID-' + row['IntegratedCircuitCardIdentity'], key):
            if alias in self.catalog:
                kind, link = self.catalog[alias]
                if kind == 'l':
                    return Path(link.decode()).name
        return self.system_bundles.get(row['IntegratedCircuitCardIdentity'], 'TMobile_MVNO_US.bundle')

    @property
    def alias(self):
        base = '310240_GID1-' + self.args.gid1 if self.args.gid1 else self.baseline_key
        qualified = self.args.gid1_iccid or self.args.tmobile_wholesale
        return base + '_ID-' + self.iccid if qualified else base

    def trigger(self, args, assets, work, info, identities, bundle):
        if self.trigger_error:
            raise RuntimeError('No validated trigger in this sandbox')
        if args.sims != 'all':
            selected = next((row for row in self.rows if row['Slot'] in carrier.SLOT_CHOICES[args.sims]), None)
            if selected is not None:
                self.iccid = selected['IntegratedCircuitCardIdentity']
        else:
            eligible = [row for row in self.rows
                        if row.get('CFBundleIdentifier') == 'com.apple.TMobile_MVNO_US']
            if eligible:
                self.iccid = eligible[0]['IntegratedCircuitCardIdentity']
        path = work / 'gid-trigger.ipcc'
        path.write_bytes(b'isolated transport boundary')
        return path, {'sha256': carrier.digest(path.read_bytes())}

    async def transfer(self, device, run, payload=None, expected=None, recovery=False, before_commit=None):
        self.connected(device)
        run.mkdir()
        if failure := self.transfer_failures.pop(run.name, None):
            phase, change = failure
            journal = dict(target=carrier.TARGET, udid_hash=carrier.digest(device.udid.encode()),
                           source='airlift-src-' + 'a' * 20, link='airlift-link-' + 'a' * 20,
                           exported='airlift-saved-' + 'a' * 20, phase=phase, complete=False)
            if phase == 'staging':
                carrier.write_tree_zip(run / 'books.zip', {})
                carrier.save_json(run / 'books.json', {'hash': carrier.tree_hash({}), 'existed': False})
                journal['requires_recovery'] = True
            carrier.save_json(run / 'journal.json', journal)
            if change == 'catalog':
                self.catalog['later-change'] = ('f', b'preserve this current change')
            elif change == 'identity':
                next(row for row in self.rows if row['MCC'] + row['MNC'] == '310240')[
                    'IntegratedCircuitCardIdentity'] = '8901000000000000099'
            elif change == 'device':
                self.wrong_reconnect = True
            device.disconnected = True
            raise ConnectionResetError('Connection lost')
        if run.name == 'apply' and self.precommit:
            self.precommit()
        original = dict(self.catalog)
        carrier.write_tree_zip(run / 'original.zip', original)
        carrier.write_tree_zip(run / 'books.zip', {})
        carrier.save_json(run / 'books.json', {'hash': carrier.tree_hash({}), 'existed': False})
        journal = dict(target=carrier.TARGET, udid_hash=carrier.digest(device.udid.encode()),
                       original_hash=carrier.tree_hash(original), complete=False, requires_recovery=True,
                       phase='backup-saved', exported='airlift-saved-' + 'a' * 20,
                       payload_hash=carrier.tree_hash(payload) if payload is not None else None)
        carrier.save_json(run / 'journal.json', journal)
        carrier.require(expected is None or (expected(original) if callable(expected) else expected == original),
                        'Catalog changed before commit')
        if before_commit:
            await before_commit()
        if payload is not None:
            carrier.validate_tree(payload)
            self.catalog = dict(payload)
            self.catalog_writes += 1
            additions = [name for name in payload if '_GID1-' in name and name not in original]
            self.alias_commits += len(additions)
            self.committed_aliases.extend(additions)
        journal.update(phase='placement-observed', complete=True, requires_recovery=False, books_restored=True)
        carrier.save_json(run / 'journal.json', journal)
        return original

    async def rescan(self, device, trigger, stage):
        self.connected(device)
        mode = self.failure_modes.get(self.iccid, self.mode)
        self.rescans.append((stage.name, tuple(row['IntegratedCircuitCardIdentity'] for row in self.rows)))
        if stage.name == 'rescan':
            if not mode.startswith('drop'):
                self.catalog['later-change'] = ('f', b'preserve this current change')
            if mode == 'interrupt':
                raise KeyboardInterrupt()
            if mode == 'missing':
                return {'ipcc_installation_completed': True, 'log_error': 'stream lost'}
        text = ''
        for row in self.rows:
            index = 1 if row['Slot'] == 'kOne' else 2
            iccid = row['IntegratedCircuitCardIdentity']
            target = iccid == self.iccid
            bundle = operator = self.selected_bundle(row)
            verification = operator_verification = 'Success'
            if stage.name in ('rescan', 'rescan-reconnected'):
                if target:
                    if mode in ('reject', 'drop-reject'):
                        bundle = operator = 'TMobile_MVNO_US.bundle'
                    if mode in ('signature', 'drop-signature'):
                        verification = operator_verification = 'Failed'
                    if mode == 'operator-reject':
                        operator = 'TMobile_MVNO_US.bundle'
                    if mode == 'operator-signature':
                        operator_verification = 'Failed'
                else:
                    if mode in ('other-slot', 'drop-other-slot'):
                        bundle = operator = 'WrongOther.bundle'
                    if mode == 'operator-other-slot':
                        operator = 'WrongOther.bundle'
            if stage.name in ('undo-rescan', 'undo-rescan-reconnected'):
                if target and mode == 'undo-carrier-signature':
                    verification = 'Failed'
                if target and mode == 'undo-operator-signature':
                    operator_verification = 'Failed'
                if not target and mode == 'undo-operator-other-slot':
                    operator = 'WrongOther.bundle'
            tmobile = row['MCC'] + row['MNC'] == '310240'
            text += (baseline_log(bundle, self.key(iccid), index) if tmobile and stage.name == 'baseline' else
                     bundle_block(index, bundle, verification))
            if mode != 'operator-missing' or stage.name != 'rescan':
                text += bundle_block(index, operator, operator_verification, kind='Operator')
            row['CFBundleIdentifier'] = 'com.apple.' + bundle.removesuffix('.bundle')
        # A phone with no active profiles may produce a genuinely empty selection log.
        (stage / 'commcenter.log').write_text(text, encoding='utf-8')
        dropping = ((stage.name == 'rescan' and mode.startswith('drop') and mode != 'drop-undo') or
                    (stage.name == 'undo-rescan' and mode == 'drop-undo') or
                    (stage.name == 'rescan-reconnected' and mode == 'drop-twice'))
        if dropping:
            if mode == 'drop-alias':
                self.catalog[self.alias] = ('l', b'not our alias any more')
            if mode == 'drop-catalog':
                self.catalog['later-change'] = ('f', b'preserve this current change')
            if mode == 'drop-sim':
                self.row(self.iccid)['IntegratedCircuitCardIdentity'] = '8901000000000000099'
            if mode == 'drop-device':
                self.wrong_reconnect = True
            device.disconnected = True
            return {'ipcc_installation_completed': True, 'log_error': 'ConnectionTerminatedError',
                    'log_interrupted': True}
        return {'ipcc_installation_completed': True}

    def owner(self, iccid=ROWS[1]['IntegratedCircuitCardIdentity']):
        wanted = carrier.digest(iccid.encode())
        owners = []
        for path in self.root.glob('*/gid-alias.json'):
            record = carrier.read_json(path)
            if record.get('kind') != 'gid1-alias':
                continue
            identity = record.get('identities', {}).get(record.get('slot'), {})
            if identity.get('iccid_hash') == wanted:
                owners.append(path.parent)
        if len(owners) != 1:
            raise AssertionError(f'Expected exactly one journal for the requested identity, found {len(owners)}')
        return owners[0]

    def record(self, iccid=ROWS[1]['IntegratedCircuitCardIdentity']):
        return carrier.read_json(self.owner(iccid) / 'gid-alias.json')

    def removal_owner(self):
        owners = [path.parent for path in self.root.glob('*/gid-alias.json')
                  if carrier.read_json(path).get('kind') == 'wholesale-removal']
        if len(owners) != 1:
            raise AssertionError(f'Expected exactly one removal journal, found {len(owners)}')
        return owners[0]

    def removal_record(self):
        return carrier.read_json(self.removal_owner() / 'gid-alias.json')

    async def resume_removal(self, generic=False):
        args = SimpleNamespace(**vars(self.args))
        args.gid1 = None
        args.gid1_iccid = args.tmobile_wholesale = args.remove_tmobile_wholesale = args.cleanup_overrides = False
        args.undo_gid1 = None if generic else self.removal_owner()
        args.recover = Path('AUTO') if generic else None
        return await carrier.execute_with_retry(args, None)

    async def apply(self):
        return await carrier.execute_with_retry(self.args, None)

    async def undo(self, iccid=ROWS[1]['IntegratedCircuitCardIdentity']):
        args = SimpleNamespace(**vars(self.args))
        args.gid1, args.undo_gid1 = None, self.owner(iccid)
        args.gid1_iccid = False
        args.tmobile_wholesale = args.remove_tmobile_wholesale = args.cleanup_overrides = False
        return await carrier.execute_with_retry(args, None)

    async def automatic(self, sims='all'):
        self.args.gid1 = None
        self.args.gid1_iccid = False
        self.args.tmobile_wholesale = True
        self.args.sims = sims
        return await self.apply()

    async def remove(self, status=False, sims='all'):
        args = SimpleNamespace(**vars(self.args))
        args.gid1, args.undo_gid1 = None, None
        args.gid1_iccid = args.tmobile_wholesale = args.cleanup_overrides = False
        args.remove_tmobile_wholesale, args.status, args.sims = True, status, sims
        return await carrier.execute_with_retry(args, None)

    async def cleanup(self, status=False, sims='all'):
        args = SimpleNamespace(**vars(self.args))
        args.gid1, args.undo_gid1 = None, None
        args.gid1_iccid = args.tmobile_wholesale = args.remove_tmobile_wholesale = False
        args.cleanup_overrides, args.status, args.sims = True, status, sims
        return await carrier.execute_with_retry(args, None)


class GidProofTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'commcenter.log'
        self.identities = carrier.gid_identities(ROWS)

    def test_gid_normalization_and_byte_boundaries(self):
        self.assertEqual(carrier.normalize_gid1(' 0xfa69 '), 'FA69')
        self.assertEqual(carrier.normalize_gid1('00'), '00')
        self.assertEqual(carrier.normalize_gid1('ab' * 16), 'AB' * 16)
        for value in ('', '0x', '6', '699', 'ab' * 17, '../69', 'ＧＩＤ', '6g'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                carrier.normalize_gid1(value)

    def test_identity_binding_rejects_missing_ambiguous_and_mismatched_values(self):
        cases = [ROWS + [ROWS[0]], [dict(ROWS[0], IntegratedCircuitCardIdentity='')],
                 [dict(ROWS[0], InternationalMobileSubscriberIdentity='310240000000001')],
                 [dict(ROWS[0], MCC='３１１')], [dict(ROWS[0], Slot='unknown')]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                carrier.gid_identities(rows)

    def test_correct_bundle_requires_correct_slot_signature_and_complete_rescan(self):
        good = bundle_block(1, 'Verizon_LTE_US.bundle') + bundle_block(2, BUNDLE)
        self.path.write_text(good, encoding='utf-8')
        results = carrier.gid_selection(self.path, self.identities, {'ipcc_installation_completed': True})
        self.assertEqual(results['kTwo']['resolved'], SYSTEM + BUNDLE)
        self.assertTrue(results['kTwo']['verified'])
        for text in (good.replace('Verification Result: Success', 'Verification Result: Failed'),
                     good.replace(carrier.PARENT + '/BundleLinks/', '/wrong-directory/'),
                     good.replace(SYSTEM + BUNDLE, SYSTEM + 'nested/' + BUNDLE),
                     bundle_block(1, 'Verizon_LTE_US.bundle'),
                     good + bundle_block(2, 'TMobile_MVNO_US.bundle')):
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                carrier.gid_selection(self.path, self.identities, {'ipcc_installation_completed': True})
        self.path.write_text(good, encoding='utf-8')
        for status in ({}, {'ipcc_installation_completed': True, 'log_error': 'lost'}):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                carrier.gid_selection(self.path, self.identities, status)

    def test_baseline_key_requires_explicit_chosen_lookup_not_score_inference(self):
        self.path.write_text(baseline_log(), encoding='utf-8')
        self.assertEqual(carrier.gid_baseline_key(self.path, 'kTwo', ALIAS), ALIAS)
        for text in (baseline_log(key='310240'), baseline_log().replace(
                     "No match in user's, choosing System match", 'Best match URL: <private>'),
                     bundle_block(2, 'TMobile_MVNO_US.bundle')):
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                carrier.gid_baseline_key(self.path, 'kTwo', ALIAS)

    def test_no_active_slots_still_requires_fresh_activity_and_successful_installation(self):
        for text, status in (('', {'ipcc_installation_completed': True}),
                             ('CommCenter activity', {'ipcc_installation_completed': False})):
            self.path.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                carrier.gid_selection(self.path, {}, status)


class GidTransactionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.quiet = contextlib.ExitStack()
        self.quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(self.quiet.close)

    async def test_apply_then_scoped_undo_preserves_later_unrelated_changes(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            self.assertEqual(sandbox.catalog[ALIAS], carrier.bundle_link(BUNDLE))
            self.assertEqual(sandbox.record()['state'], 'applied_verified')
            for row in ROWS:
                for key in ('InternationalMobileSubscriberIdentity', 'IntegratedCircuitCardIdentity'):
                    self.assertNotIn(row[key], (sandbox.owner() / 'gid-alias.json').read_text())
            expected = dict(sandbox.catalog)
            expected.pop(ALIAS)
            await sandbox.undo()
            self.assertEqual(sandbox.catalog, expected)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_iccid_qualified_trial_and_undo_preserve_changes_and_hide_identifiers(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.gid1_iccid = True
            with contextlib.redirect_stdout(io.StringIO()) as output:
                await sandbox.apply()
                self.assertEqual(sandbox.catalog[QUALIFIED_ALIAS], carrier.bundle_link(BUNDLE))
                record = sandbox.record()
                self.assertTrue(record['iccid_qualified'])
                self.assertEqual(record['baseline_key'], ALIAS)
                self.assertEqual(record['result']['kTwo']['operator']['selected'], BUNDLE)
                expected = dict(sandbox.catalog)
                expected.pop(QUALIFIED_ALIAS)
                await sandbox.undo()
            self.assertEqual(sandbox.catalog, expected)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertEqual(sandbox.record()['undo_result']['kTwo']['operator']['selected'], 'TMobile_MVNO_US.bundle')
            self.assertTrue(sandbox.record()['rollback_verified'])
            self.assertNotIn(ROWS[1]['IntegratedCircuitCardIdentity'], output.getvalue())
            self.assertNotIn(ROWS[1]['InternationalMobileSubscriberIdentity'], output.getvalue())

    async def test_iccid_qualified_operator_failure_rolls_back_once(self):
        for mode in ('operator-reject', 'operator-signature', 'operator-missing', 'operator-other-slot'):
            case = self.root / mode
            case.mkdir()
            with self.subTest(mode=mode), Sandbox(case) as sandbox:
                sandbox.args.gid1_iccid = True
                sandbox.mode = mode
                with self.assertRaises(RuntimeError):
                    await sandbox.apply()
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertNotIn(QUALIFIED_ALIAS, sandbox.catalog)
                self.assertEqual(sandbox.catalog, dict(sandbox.original, **{'later-change': ('f', b'preserve this current change')}))
                self.assertEqual(sandbox.record()['state'], 'rolled_back')
                self.assertTrue(sandbox.record()['undo_result']['kTwo']['operator']['verified'])

    async def test_iccid_qualified_undo_refuses_a_mismatched_iccid_binding(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.gid1_iccid = True
            await sandbox.apply()
            record = sandbox.record()
            record['alias'] = ALIAS + '_ID-' + ROWS[0]['IntegratedCircuitCardIdentity']
            carrier.save_json(sandbox.owner() / 'gid-alias.json', record)
            before = dict(sandbox.catalog)
            with self.assertRaises(RuntimeError):
                await sandbox.undo()
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.record()['state'], 'applied_verified')

    async def test_iccid_qualified_undo_works_without_the_original_sim(self):
        for no_sims in (False, True):
            case = self.root / str(no_sims)
            case.mkdir()
            with self.subTest(no_sims=no_sims), Sandbox(case) as sandbox:
                sandbox.args.gid1_iccid = True
                await sandbox.apply()
                sandbox.rows = [] if no_sims else sandbox.rows[:1]
                await sandbox.undo()
                self.assertNotIn(QUALIFIED_ALIAS, sandbox.catalog)
                self.assertEqual(sandbox.record()['state'], 'undone')
                self.assertEqual(sandbox.record()['undo_selection_status'], 'no_active_sims' if no_sims else 'verified')


    async def test_failed_outcomes_and_interrupts_undo_once_without_apply_retry(self):
        for mode in ('reject', 'signature', 'missing', 'other-slot', 'interrupt'):
            case = self.root / mode
            case.mkdir()
            with self.subTest(mode=mode), Sandbox(case) as sandbox:
                sandbox.mode = mode
                with self.assertRaises(KeyboardInterrupt if mode == 'interrupt' else RuntimeError):
                    await sandbox.apply()
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertEqual(sandbox.catalog, dict(sandbox.original, **{'later-change': ('f', b'preserve this current change')}))
                self.assertEqual(sandbox.record()['state'], 'rolled_back')
                self.assertTrue(sandbox.record()['rollback_verified'])

    async def test_preexisting_entry_is_not_owned_even_when_bytes_are_identical(self):
        with Sandbox(self.root) as sandbox:
            sandbox.catalog[ALIAS] = carrier.bundle_link(BUNDLE)
            before = dict(sandbox.catalog)
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.alias_commits, 0)
            self.assertFalse(sandbox.record()['ownership_established'])
            self.assertEqual(sandbox.record()['state'], 'aborted')

    async def test_identical_alias_added_before_authorization_is_not_deleted(self):
        with Sandbox(self.root) as sandbox:
            sandbox.precommit = lambda: sandbox.catalog.__setitem__(ALIAS, carrier.bundle_link(BUNDLE))
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            self.assertEqual(sandbox.catalog[ALIAS], carrier.bundle_link(BUNDLE))
            self.assertEqual(sandbox.alias_commits, 0)
            self.assertFalse(sandbox.record()['ownership_established'])
            self.assertEqual(sandbox.record()['state'], 'aborted')

    async def test_same_plmn_other_slot_and_closed_build_stop_without_an_owner(self):
        for closed in (False, True):
            with self.subTest(closed=closed), Sandbox(self.root) as sandbox:
                if closed:
                    sandbox.info.update(ProductVersion='27.2', BuildVersion='24B5099f')
                else:
                    sandbox.rows[0].update(MCC='310', MNC='240', InternationalMobileSubscriberIdentity='310240000000001')
                with self.assertRaises(RuntimeError):
                    await sandbox.apply()
                self.assertEqual(sandbox.catalog, sandbox.original)
                self.assertEqual(list(self.root.glob('*/gid-alias.json')), [])

    async def test_unavailable_trigger_aborts_before_device_changes(self):
        with Sandbox(self.root) as sandbox:
            sandbox.trigger_error = True
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.record()['state'], 'aborted')
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])
            self.assertFalse(sandbox.record().get('rollback_verified', False))

    async def test_sim_swap_before_commit_never_adds_alias(self):
        with Sandbox(self.root) as sandbox:
            sandbox.precommit = lambda: sandbox.rows[1].update(IntegratedCircuitCardIdentity='8901000000000000099')
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            self.assertEqual(sandbox.alias_commits, 0)
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.record()['state'], 'aborted')

    async def test_undo_with_original_sim_removed_and_with_no_active_sims(self):
        for no_sims in (False, True):
            case = self.root / str(no_sims)
            case.mkdir()
            with self.subTest(no_sims=no_sims), Sandbox(case) as sandbox:
                await sandbox.apply()
                sandbox.rows = [] if no_sims else sandbox.rows[:1]
                await sandbox.undo()
                self.assertNotIn(ALIAS, sandbox.catalog)
                self.assertEqual(sandbox.record()['state'], 'undone')
                self.assertEqual(sandbox.record()['undo_selection_status'], 'no_active_sims' if no_sims else 'verified')

    async def test_changed_owned_entry_is_refused_and_kept_recoverable(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            sandbox.catalog[ALIAS] = ('l', b'changed by another operation')
            before = dict(sandbox.catalog)
            with self.assertRaises(RuntimeError):
                await sandbox.undo()
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.record()['state'], 'rollback_required')
            self.assertFalse(sandbox.record().get('rollback_verified', False))

    async def test_absent_entry_undo_is_idempotent_but_requires_verification(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            sandbox.catalog.pop(ALIAS)
            before = dict(sandbox.catalog)
            await sandbox.undo()
            await sandbox.undo()
            self.assertEqual(sandbox.catalog, before)
            self.assertTrue(sandbox.record()['rollback_verified'])

    async def test_corrupt_backup_and_wrong_device_never_remove_alias(self):
        for wrong_device in (False, True):
            case = self.root / str(wrong_device)
            case.mkdir()
            with self.subTest(wrong_device=wrong_device), Sandbox(case) as sandbox:
                await sandbox.apply()
                if wrong_device:
                    sandbox.device.udid = 'different-device'
                else:
                    (sandbox.owner() / 'snapshot' / 'original.zip').write_bytes(b'broken backup')
                before = dict(sandbox.catalog)
                with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                    await sandbox.undo()
                self.assertEqual(sandbox.catalog, before)
                self.assertEqual(sandbox.record()['state'], 'applied_verified')

    async def test_normal_write_is_blocked_but_generic_recovery_leaves_a_healthy_installation(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            args = SimpleNamespace(**vars(sandbox.args))
            args.gid1 = None
            with self.assertRaises(RuntimeError):
                await carrier.execute_with_retry(args, None)
            self.assertIn(ALIAS, sandbox.catalog)
            args.recover = Path('AUTO')
            self.assertEqual(await carrier.execute_with_retry(args, None), 0)
            self.assertIn(ALIAS, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'applied_verified')

    async def test_read_only_plan_creates_no_journal_or_catalog_stage(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.status = True
            await sandbox.apply()
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(list(self.root.iterdir()), [])

    async def test_wrong_baseline_matching_key_never_commits_alias(self):
        with Sandbox(self.root) as sandbox:
            sandbox.baseline_key = '310240'
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.alias_commits, 0)
            self.assertEqual(sandbox.record()['state'], 'aborted')

    async def test_failed_books_repair_keeps_alias_and_incomplete_recovery(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            journal = sandbox.owner() / 'apply' / 'journal.json'
            stage = carrier.read_json(journal)
            stage['books_restored'] = False
            carrier.save_json(journal, stage)
            before = dict(sandbox.catalog)
            with patch.object(carrier, 'restore_books', AsyncMock(side_effect=RuntimeError('Books repair failed'))):
                with self.assertRaises(RuntimeError):
                    await sandbox.undo()
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.record()['state'], 'rollback_required')
            self.assertFalse(sandbox.record().get('rollback_verified', False))
            self.assertIn(journal.parent, carrier.pending(self.root, sandbox.device.udid))

    async def test_ambiguous_crash_never_blindly_replaces_current_catalog(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            journal = sandbox.owner() / 'apply' / 'journal.json'
            stage = carrier.read_json(journal)
            stage.update(complete=False, phase='final-authorized', requires_recovery=True,
                         source='airlift-src-' + 'b' * 20, books_restored=False)
            carrier.save_json(journal, stage)
            before = dict(sandbox.catalog)
            with patch.object(carrier, 'exists', AsyncMock(return_value={'st_ifmt': 'S_IFDIR'})):
                with self.assertRaises(RuntimeError):
                    await sandbox.undo()
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.record()['state'], 'rollback_required')
            self.assertFalse(sandbox.record().get('rollback_verified', False))
            self.assertIn(journal.parent, carrier.pending(self.root, sandbox.device.udid))

    async def test_planning_crash_can_cancel_without_an_available_trigger(self):
        with Sandbox(self.root) as sandbox:
            sandbox.trigger_error = True
            with self.assertRaises(RuntimeError):
                await sandbox.apply()
            record = sandbox.record()
            record['state'] = 'planning'
            carrier.save_json(sandbox.owner() / 'gid-alias.json', record)
            await sandbox.undo()
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.record()['state'], 'aborted')
            self.assertFalse(sandbox.record().get('rollback_verified', False))
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_process_crash_after_verified_placement_is_recovered_from_stage_journal(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            journal = sandbox.owner() / 'apply' / 'journal.json'
            stage = carrier.read_json(journal)
            stage.update(complete=False, phase='final-authorized', requires_recovery=True,
                         source='airlift-src-' + 'b' * 20, books_restored=False)
            carrier.save_json(journal, stage)
            record = sandbox.record()
            record.update(state='apply_started')
            carrier.save_json(sandbox.owner() / 'gid-alias.json', record)
            await sandbox.undo()
            self.assertNotIn(ALIAS, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertTrue(carrier.read_json(journal)['placement_recovered'])

    async def test_automatic_discovery_uses_the_actual_key_and_qualifies_the_correct_sim_in_either_slot(self):
        for slot in ('kOne', 'kTwo'):
            case = self.root / slot
            case.mkdir()
            with self.subTest(slot=slot), Sandbox(case) as sandbox:
                if slot == 'kOne':
                    sandbox.rows[0]['Slot'], sandbox.rows[1]['Slot'] = 'kTwo', 'kOne'
                sandbox.baseline_key = '310240_GID1-7A'
                await sandbox.automatic()
                record = sandbox.record()
                expected = sandbox.baseline_key + '_ID-' + ROWS[1]['IntegratedCircuitCardIdentity']
                self.assertEqual(record['slot'], slot)
                self.assertEqual(record['gid1'], '7A')
                self.assertEqual(sandbox.catalog[expected], carrier.bundle_link(BUNDLE))
                self.assertNotIn(QUALIFIED_ALIAS, sandbox.catalog)
                self.assertEqual(record['result'][slot]['operator']['selected'], BUNDLE)
                other_slot = 'kTwo' if slot == 'kOne' else 'kOne'
                self.assertEqual(record['result'][other_slot]['operator']['selected'], 'Verizon_LTE_US.bundle')
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])
                self.assertEqual(carrier.gid_active(case, sandbox.device.udid), [sandbox.owner()])

    async def test_automatic_explicit_slot_isolates_same_plmn_esims(self):
        for sims, slot in (('1', 'kOne'), ('2', 'kTwo')):
            case = self.root / sims
            case.mkdir()
            with self.subTest(sims=sims), Sandbox(case) as sandbox:
                sandbox.rows[0].update(MCC='310', MNC='240', CFBundleIdentifier='com.apple.TMobile_MVNO_US',
                                       InternationalMobileSubscriberIdentity='310240000000001')
                selected = next(row for row in sandbox.rows if row['Slot'] == slot)
                other = next(row for row in sandbox.rows if row['Slot'] != slot)
                other_before = dict(other)
                sandbox.baseline_key = '310240_GID1-7A'
                expected = sandbox.baseline_key + '_ID-' + selected['IntegratedCircuitCardIdentity']
                self.assertEqual(await sandbox.automatic(sims), 0)
                record = sandbox.record(selected['IntegratedCircuitCardIdentity'])
                self.assertEqual(record['slot'], slot)
                self.assertEqual(record['baseline_key'], sandbox.baseline_key)
                self.assertEqual(sandbox.catalog[expected], carrier.bundle_link(BUNDLE))
                self.assertEqual({name for name in sandbox.catalog if name.startswith(sandbox.baseline_key + '_ID-')},
                                 {expected})
                self.assertEqual(selected['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
                self.assertEqual(other, other_before)
                self.assertEqual(record['result'][slot]['selected'], BUNDLE)
                self.assertEqual(record['result'][slot]['operator']['selected'], BUNDLE)
                self.assertEqual(record['result'][other['Slot']], record['baseline'][other['Slot']])
                self.assertTrue(carrier.gid_healthy(record))
                self.assertEqual(await sandbox.automatic(sims), 0)
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertEqual(sandbox.catalog_writes, 1)
                self.assertEqual(await sandbox.remove(), 0)
                self.assertNotIn(expected, sandbox.catalog)
                self.assertEqual(sandbox.record(selected['IntegratedCircuitCardIdentity'])['state'], 'undone')
                self.assertEqual(selected['CFBundleIdentifier'], 'com.apple.TMobile_MVNO_US')
                self.assertEqual(other, other_before)
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])
                self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_dual_tmobile_other_carrier_or_operator_change_requires_rollback(self):
        for sims in ('1', '2'):
            for mode in ('other-slot', 'operator-other-slot'):
                case = self.root / (sims + '-' + mode)
                case.mkdir()
                with self.subTest(sims=sims, mode=mode), Sandbox(case) as sandbox:
                    sandbox.rows[0].update(MCC='310', MNC='240', CFBundleIdentifier='com.apple.TMobile_MVNO_US',
                                           InternationalMobileSubscriberIdentity='310240000000001')
                    sandbox.mode = mode
                    with self.assertRaises(RuntimeError):
                        await sandbox.automatic(sims)
                    record = sandbox.record(ROWS[int(sims) - 1]['IntegratedCircuitCardIdentity'])
                    self.assertEqual(record['state'], 'rolled_back')
                    self.assertNotIn(record['alias'], sandbox.catalog)
                    self.assertEqual(sandbox.alias_commits, 1)
                    self.assertTrue(all(row['CFBundleIdentifier'] == 'com.apple.TMobile_MVNO_US'
                                        for row in sandbox.rows))
                    self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])

    async def test_automatic_preview_never_exports_catalog_or_installs_trigger(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.status = True
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(list(self.root.iterdir()), [])

    async def test_automatic_unsupported_or_inactive_sims_fail_without_changes(self):
        for kind in ('foreign', 'explicit-foreign', 'inactive'):
            case = self.root / kind
            case.mkdir()
            with self.subTest(kind=kind), Sandbox(case) as sandbox:
                sandbox.args.gid1 = None
                sandbox.args.tmobile_wholesale = True
                sandbox.args.sims = 'all'
                if kind == 'foreign':
                    sandbox.rows = sandbox.rows[:1]
                else:
                    sandbox.args.sims = '1' if kind == 'explicit-foreign' else '2'
                    if kind == 'inactive':
                        sandbox.rows = sandbox.rows[:1]
                with self.assertRaises(RuntimeError):
                    await sandbox.apply()
                self.assertEqual(sandbox.catalog, sandbox.original)
                self.assertEqual(list(case.iterdir()), [])

    async def test_automatic_discovery_refuses_non_gid_and_composite_keys_before_owning_any_alias(self):
        for key in ('310240', '310240_GID1-69_ID-123', '310240_GID1-7'):
            case = self.root / key
            case.mkdir()
            with self.subTest(key=key), Sandbox(case) as sandbox:
                sandbox.baseline_key = key
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic()
                self.assertEqual(sandbox.catalog, sandbox.original)
                self.assertEqual(list(case.glob('*/gid-alias.json')), [])

    async def test_automatic_rerun_recognizes_a_manual_qualified_owner_without_rewriting_or_rescanning(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.gid1_iccid = True
            await sandbox.apply()
            original_record = (sandbox.owner() / 'gid-alias.json').read_bytes()
            original_catalog = dict(sandbox.catalog)
            sandbox.rows[0]['IntegratedCircuitCardIdentity'] = '8901000000000000099'
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic(), 0)
                sandbox.args.status = True
                with patch.dict(carrier.os.environ, CARRIERSIM_PLAN='1'):
                    self.assertEqual(await sandbox.apply(), carrier.NOTHING_TO_WRITE)
            self.assertEqual(sandbox.catalog, original_catalog)
            self.assertEqual((sandbox.owner() / 'gid-alias.json').read_bytes(), original_record)

    async def test_stock_wholesale_without_a_catalog_alias_is_not_removed_or_owned(self):
        with Sandbox(self.root) as sandbox:
            sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            original = dict(sandbox.catalog)
            self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(await sandbox.remove(), 0)
            self.assertEqual(sandbox.catalog, original)
            self.assertEqual(sandbox.catalog_writes, 0)
            record = sandbox.removal_record()
            self.assertEqual(record['state'], 'aborted')
            self.assertEqual(record['aliases'], [])
            self.assertFalse(record.get('ownership_established', False))

    async def test_install_does_not_adopt_an_unowned_wholesale_alias_but_all_removal_can_remove_it(self):
        with Sandbox(self.root) as sandbox:
            sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
            original = dict(sandbox.catalog)
            self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(sandbox.catalog, original)
            self.assertEqual(sandbox.catalog_writes, 0)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(await sandbox.remove(), 0)
            self.assertNotIn(QUALIFIED_ALIAS, sandbox.catalog)
            self.assertEqual(sandbox.catalog_writes, 1)
            records = [carrier.read_json(path) for path in self.root.glob('*/gid-alias.json')]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['kind'], 'wholesale-removal')
            self.assertFalse(records[0].get('ownership_established', False))

    async def test_all_removal_without_history_preserves_exact_non_wholesale_and_malformed_entries(self):
        with Sandbox(self.root) as sandbox:
            link = carrier.bundle_link(BUNDLE)
            iccid = ROWS[1]['IntegratedCircuitCardIdentity']
            lookalikes = {
                ALIAS: link,
                '3102_GID1-69_ID-' + iccid: link,
                '3102400_GID1-69_ID-' + iccid: link,
                '310240_GID1-6_ID-' + iccid: link,
                '310240_GID1-ab_ID-' + iccid: link,
                '310240_GID1-' + 'AB' * 17 + '_ID-' + iccid: link,
                '310240_GID1-_ID-' + iccid: link,
                ALIAS + '_ID-' + '8' * 17: link,
                ALIAS + '_ID-' + '8' * 23: link,
                QUALIFIED_ALIAS + '-extra': link,
                'nested': ('d', b''),
                'nested/' + QUALIFIED_ALIAS: link,
                '310240_IMSI-310240000000002': link,
                '310240_GID1-68_ID-' + iccid: carrier.bundle_link('TMobile_MVNO_US.bundle'),
                '310240_GID1-67_ID-' + iccid: ('f', link[1]),
                '310240_GID1-66_ID-' + iccid: ('l', b'../TMobile_Wholesale_US.bundle'),
            }
            # The unqualified rule is not selected by this active line.
            sandbox.baseline_key = '310240_GID1-70'
            selected = sandbox.qualified_alias(iccid)
            sandbox.catalog.update(lookalikes)
            sandbox.catalog[selected] = link
            sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            sandbox.system_bundles[iccid] = 'TMobile_US.bundle'
            expected = dict(sandbox.catalog)
            expected.pop(selected)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(await sandbox.remove(), 0)
            self.assertEqual(sandbox.catalog, expected)
            self.assertEqual(sandbox.catalog_writes, 1)
            self.assertEqual(sandbox.row(iccid)['CFBundleIdentifier'], 'com.apple.TMobile_US')
            self.assertEqual(sandbox.rows[0], ROWS[0])
            record = sandbox.removal_record()
            self.assertFalse(record.get('ownership_established', False))
            self.assertEqual(record['state'], 'undone')
            self.assertTrue(record['catalog_verified'])
            self.assertTrue(record['rollback_verified'])
            self.assertEqual(record['aliases'], [selected])
            for slot, bundle in (('kOne', 'Verizon_LTE_US.bundle'), ('kTwo', 'TMobile_US.bundle')):
                proof = record['undo_result'][slot]
                self.assertEqual(proof['selected'], bundle)
                self.assertTrue(proof['verified'])
                self.assertEqual(proof['operator']['selected'], bundle)
                self.assertTrue(proof['operator']['verified'])
            self.assertEqual([carrier.read_json(path).get('kind')
                              for path in self.root.glob('*/gid-alias.json')], ['wholesale-removal'])

    async def test_all_removal_discovers_both_active_same_plmn_profiles_and_an_inactive_profile(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            aliases = [sandbox.qualified_alias(row['IntegratedCircuitCardIdentity']) for row in sandbox.rows]
            aliases.append('310240_GID1-FA69_ID-8901000000000000099')
            sandbox.catalog.update({alias: carrier.bundle_link(BUNDLE) for alias in aliases})
            for row in sandbox.rows:
                row['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            self.assertEqual(await sandbox.remove(), 0)
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.catalog_writes, 1)
            self.assertTrue(all(row['CFBundleIdentifier'] == 'com.apple.TMobile_MVNO_US'
                                for row in sandbox.rows))
            record = sandbox.removal_record()
            self.assertFalse(record.get('ownership_established', False))
            self.assertEqual(record['aliases'], sorted(aliases))
            for proof in record['undo_result'].values():
                self.assertEqual(proof['selected'], 'TMobile_MVNO_US.bundle')
                self.assertEqual(proof['operator']['selected'], 'TMobile_MVNO_US.bundle')
                self.assertTrue(proof['verified'])
                self.assertTrue(proof['operator']['verified'])
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_all_removal_without_active_sims_uses_catalog_and_books_proof_without_a_trigger(self):
        with Sandbox(self.root) as sandbox:
            sandbox.rows = []
            aliases = ['31024_GID1-00_ID-' + '8' * 18,
                       '310240_GID1-' + 'AB' * 16 + '_ID-' + '9' * 22]
            sandbox.catalog.update({alias: carrier.bundle_link(BUNDLE) for alias in aliases})
            sandbox.trigger_error = True
            with patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.remove(), 0)
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(sandbox.catalog_writes, 1)
            self.assertEqual(sandbox.rescans, [])
            record = sandbox.removal_record()
            self.assertEqual(record['state'], 'undone')
            self.assertEqual(record['undo_selection_status'], 'no_active_sims')
            self.assertEqual(record['undo_result'], {})
            self.assertTrue(record['catalog_verified'])
            self.assertTrue(record['rollback_verified'])
            self.assertEqual(carrier.pending(self.root, sandbox.device.udid), [])
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_all_removal_and_cleanup_previews_without_history_are_genuinely_read_only(self):
        with Sandbox(self.root) as sandbox:
            sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
            before = dict(sandbox.catalog)
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'gid_trigger', side_effect=AssertionError('Unexpected trigger staging')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.remove(status=True), 0)
                self.assertEqual(await sandbox.cleanup(status=True), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(sandbox.catalog_writes, 0)
            self.assertEqual(sandbox.rescans, [])

    async def test_explicit_slot_removal_does_not_discover_or_adopt_a_remote_alias(self):
        with Sandbox(self.root) as sandbox:
            sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
            before = dict(sandbox.catalog)
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.remove(sims='2'), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(sandbox.catalog_writes, 0)
            sandbox.dual_mvno()
            sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            await sandbox.automatic('1')
            owner = sandbox.owner(ROWS[0]['IntegratedCircuitCardIdentity'])
            self.assertEqual(await sandbox.remove(sims='1'), 0)
            self.assertNotIn(sandbox.qualified_alias(ROWS[0]['IntegratedCircuitCardIdentity']), sandbox.catalog)
            self.assertEqual(sandbox.catalog[QUALIFIED_ALIAS], carrier.bundle_link(BUNDLE))
            self.assertEqual(sandbox.rows[1]['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
            record = carrier.read_json(owner / 'gid-alias.json')
            self.assertEqual(record['undo_result']['kTwo']['selected'], BUNDLE)
            self.assertEqual(record['undo_result']['kTwo']['operator']['selected'], BUNDLE)
            self.assertEqual([path.parent for path in self.root.glob('*/gid-alias.json')], [owner])

    async def test_removal_recovery_uses_only_saved_authorization_and_preserves_new_matching_aliases(self):
        for generic in (False, True):
            case = self.root / str(generic)
            case.mkdir()
            with self.subTest(generic=generic), Sandbox(case) as sandbox:
                sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
                sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
                sandbox.trigger_error = True
                with self.assertRaises(RuntimeError):
                    await sandbox.remove()
                record = sandbox.removal_record()
                self.assertEqual(record['state'], 'rollback_required')
                self.assertEqual(record['aliases'], [QUALIFIED_ALIAS])
                self.assertEqual(sandbox.catalog_writes, 0)
                owner = sandbox.removal_owner()
                backup = carrier.read_tree_zip(owner / 'snapshot' / 'original.zip')
                self.assertEqual(backup[QUALIFIED_ALIAS], carrier.bundle_link(BUNDLE))
                later = '310240_GID1-FA69_ID-8901000000000000099'
                sandbox.catalog[later] = carrier.bundle_link(BUNDLE)
                sandbox.catalog['later-file'] = ('f', b'preserve subsequent user changes')
                expected = dict(sandbox.catalog)
                expected.pop(QUALIFIED_ALIAS)
                sandbox.trigger_error = False
                self.assertEqual(await sandbox.resume_removal(generic=generic), 0)
                self.assertEqual(sandbox.catalog, expected)
                self.assertEqual(sandbox.catalog_writes, 1)
                record = sandbox.removal_record()
                self.assertEqual(record['aliases'], [QUALIFIED_ALIAS])
                self.assertEqual(record['state'], 'undone')
                self.assertTrue(record['rollback_verified'])
                self.assertFalse(record.get('ownership_established', False))
                self.assertEqual(record['undo_result']['kTwo']['selected'], 'TMobile_MVNO_US.bundle')
                self.assertEqual(record['undo_result']['kTwo']['operator']['selected'], 'TMobile_MVNO_US.bundle')
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])

    async def test_removal_recovery_rejects_corrupt_backups_changed_aliases_and_expanded_authorization(self):
        for fault in ('catalog-backup', 'books-backup', 'changed-alias', 'expanded-authorization'):
            case = self.root / fault
            case.mkdir()
            with self.subTest(fault=fault), Sandbox(case) as sandbox:
                sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
                sandbox.trigger_error = True
                with self.assertRaises(RuntimeError):
                    await sandbox.remove()
                owner = sandbox.removal_owner()
                if fault in ('catalog-backup', 'books-backup'):
                    name = 'original.zip' if fault == 'catalog-backup' else 'books.zip'
                    with zipfile.ZipFile(owner / 'snapshot' / name, 'a') as backup:
                        backup.writestr(carrier.zi('unexpected-backup-entry', 'f'), b'valid ZIP, wrong tree hash')
                elif fault == 'changed-alias':
                    sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link('TMobile_MVNO_US.bundle')
                else:
                    later = '310240_GID1-FA69_ID-8901000000000000099'
                    sandbox.catalog[later] = carrier.bundle_link(BUNDLE)
                    record = sandbox.removal_record()
                    record['aliases'] = sorted(record['aliases'] + [later])
                    carrier.save_json(owner / 'gid-alias.json', record)
                before = dict(sandbox.catalog)
                sandbox.trigger_error = False
                with self.assertRaises((RuntimeError, zipfile.BadZipFile)):
                    await sandbox.resume_removal()
                self.assertEqual(sandbox.catalog, before)
                self.assertEqual(sandbox.catalog_writes, 0)
                self.assertNotEqual(sandbox.removal_record()['state'], 'undone')

    async def test_discovered_removal_disconnect_verifies_without_replaying_catalog_writes(self):
        for boundary in ('readback', 'selection'):
            case = self.root / boundary
            case.mkdir()
            with self.subTest(boundary=boundary), Sandbox(case) as sandbox:
                sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
                sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
                if boundary == 'readback':
                    sandbox.transfer_failures['undo-readback'] = ('staging', None)
                else:
                    sandbox.mode = 'drop-undo'
                self.assertEqual(await sandbox.remove(), 0)
                self.assertEqual(sandbox.catalog, sandbox.original)
                self.assertEqual(sandbox.catalog_writes, 1)
                self.assertEqual(sandbox.alias_commits, 0)
                record = sandbox.removal_record()
                self.assertEqual(record['state'], 'undone')
                self.assertTrue(record['verification_connection_errors'])
                self.assertTrue(record['rollback_verified'])
                self.assertEqual(record['undo_result']['kTwo']['operator']['selected'], 'TMobile_MVNO_US.bundle')
                self.assertEqual(carrier.pending(case, sandbox.device.udid), [])
                self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_discovered_removal_requires_signed_carrier_operator_and_unchanged_unaffected_selection(self):
        for mode in ('undo-carrier-signature', 'undo-operator-signature', 'undo-operator-other-slot'):
            case = self.root / mode
            case.mkdir()
            with self.subTest(mode=mode), Sandbox(case) as sandbox:
                sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
                sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
                sandbox.mode = mode
                with self.assertRaises(RuntimeError):
                    await sandbox.remove()
                self.assertEqual(sandbox.catalog, sandbox.original)
                self.assertEqual(sandbox.catalog_writes, 1)
                record = sandbox.removal_record()
                self.assertEqual(record['state'], 'rollback_required')
                self.assertFalse(record.get('rollback_verified', False))
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [sandbox.removal_owner()])

    async def test_automatic_removal_is_scoped_and_can_find_ownership_after_the_sim_is_removed(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.automatic()
            original = dict(sandbox.catalog)
            expected = dict(original)
            expected.pop(sandbox.alias)
            self.assertEqual(await sandbox.remove(status=True), 0)
            self.assertEqual(sandbox.catalog, original)
            sandbox.rows = sandbox.rows[:1]
            self.assertEqual(await sandbox.remove(), 0)
            self.assertEqual(sandbox.catalog, expected)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertEqual(await sandbox.remove(), 0)

    async def test_carrier_switch_disconnect_verifies_on_a_fresh_client_without_reapplying(self):
        with Sandbox(self.root) as sandbox:
            sandbox.mode = 'drop'
            await sandbox.automatic()
            self.assertEqual(sandbox.alias_commits, 1)
            self.assertEqual(sandbox.catalog_writes, 1)
            self.assertEqual(sandbox.catalog[sandbox.alias], carrier.bundle_link(BUNDLE))
            self.assertTrue(carrier.gid_healthy(sandbox.record()))
            self.assertTrue(sandbox.record()['verification_connection_errors'])
            self.assertEqual(sandbox.record()['result']['kTwo']['operator']['selected'], BUNDLE)
            self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_completed_apply_readback_disconnect_finishes_without_reapplying(self):
        for phase in ('created', 'staging'):
            case = self.root / phase
            case.mkdir()
            with self.subTest(phase=phase), Sandbox(case) as sandbox:
                sandbox.rows = [dict(ROWS[1], Slot='kOne'), dict(ROWS[0], Slot='kTwo')]
                sandbox.transfer_failures['readback'] = (phase, None)
                self.assertEqual(await sandbox.automatic(), 0)
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertEqual(sandbox.catalog_writes, 1)
                self.assertEqual(sandbox.catalog[sandbox.alias], carrier.bundle_link(BUNDLE))
                self.assertTrue(carrier.gid_healthy(sandbox.record()))
                self.assertEqual(sandbox.record()['result']['kOne']['operator']['selected'], BUNDLE)
                self.assertEqual(sandbox.record()['result']['kTwo']['selected'], 'Verizon_LTE_US.bundle')
                self.assertEqual(carrier.pending(case, sandbox.device.udid), [])
                self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_completed_removal_readback_disconnect_does_not_remove_twice(self):
        for phase in ('created', 'staging'):
            case = self.root / phase
            case.mkdir()
            with self.subTest(phase=phase), Sandbox(case) as sandbox:
                await sandbox.automatic()
                sandbox.transfer_failures['undo-readback'] = (phase, None)
                self.assertEqual(await sandbox.remove(), 0)
                self.assertNotIn(sandbox.alias, sandbox.catalog)
                self.assertEqual(sandbox.catalog_writes, 2)
                self.assertEqual(sandbox.record()['state'], 'undone')
                self.assertTrue(sandbox.record()['rollback_verified'])
                self.assertEqual(carrier.pending(case, sandbox.device.udid), [])

    async def test_readback_reconnect_does_not_accept_changed_catalog_sim_or_phone(self):
        for change in ('catalog', 'identity', 'device'):
            case = self.root / change
            case.mkdir()
            with self.subTest(change=change), Sandbox(case) as sandbox:
                sandbox.transfer_failures['readback'] = ('created', change)
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic()
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertFalse(carrier.gid_healthy(sandbox.record()))
                if change == 'device':
                    self.assertEqual(sandbox.catalog[sandbox.alias], carrier.bundle_link(BUNDLE))
                    self.assertEqual(sandbox.record()['state'], 'rollback_required')
                else:
                    self.assertNotIn(sandbox.alias, sandbox.catalog)
                    self.assertEqual(sandbox.record()['state'], 'rolled_back')
                if change == 'catalog':
                    self.assertEqual(sandbox.catalog['later-change'], ('f', b'preserve this current change'))

    async def test_second_readback_disconnect_rolls_back_without_reapplying(self):
        with Sandbox(self.root) as sandbox:
            sandbox.transfer_failures.update(readback=('created', None),
                                             **{'rescan-reconnect-catalog': ('created', None)})
            with self.assertRaises(ConnectionResetError):
                await sandbox.automatic()
            self.assertEqual(sandbox.alias_commits, 1)
            self.assertNotIn(sandbox.alias, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'rolled_back')
            self.assertEqual(carrier.pending(self.root, sandbox.device.udid), [])

    async def test_reconnected_verification_still_rejects_signature_target_other_slot_and_sim_changes(self):
        for mode in ('drop-signature', 'drop-reject', 'drop-other-slot', 'drop-sim', 'drop-twice'):
            case = self.root / mode
            case.mkdir()
            with self.subTest(mode=mode), Sandbox(case) as sandbox:
                sandbox.mode = mode
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic()
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertNotIn(sandbox.alias, sandbox.catalog)
                self.assertEqual(sandbox.record()['state'], 'rolled_back')
                self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_changed_reconnect_catalog_is_rejected_and_unrelated_changes_survive_rollback(self):
        with Sandbox(self.root) as sandbox:
            sandbox.mode = 'drop-catalog'
            with self.assertRaises(RuntimeError):
                await sandbox.automatic()
            self.assertEqual(sandbox.catalog, dict(sandbox.original, **{'later-change': ('f', b'preserve this current change')}))
            self.assertEqual(sandbox.alias_commits, 1)
            self.assertEqual(sandbox.record()['state'], 'rolled_back')

    async def test_changed_alias_and_a_different_reconnected_phone_are_never_overwritten(self):
        for mode in ('drop-alias', 'drop-device'):
            case = self.root / mode
            case.mkdir()
            with self.subTest(mode=mode), Sandbox(case) as sandbox:
                sandbox.mode = mode
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic()
                expected = ('l', b'not our alias any more') if mode == 'drop-alias' else carrier.bundle_link(BUNDLE)
                self.assertEqual(sandbox.catalog[sandbox.alias], expected)
                self.assertEqual(sandbox.alias_commits, 1)
                self.assertEqual(sandbox.record()['state'], 'rollback_required')

    async def test_undo_disconnect_verifies_removal_without_repeating_a_catalog_write(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.automatic()
            sandbox.mode = 'drop-undo'
            self.assertEqual(await sandbox.remove(), 0)
            self.assertNotIn(sandbox.alias, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertTrue(sandbox.record()['verification_connection_errors'])
            self.assertEqual(sandbox.catalog_writes, 2)
            self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_generic_recovery_still_undoes_an_interrupted_installation(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.automatic()
            record = sandbox.record()
            record.update(state='apply_started', selection_verified=False)
            carrier.save_json(sandbox.owner() / 'gid-alias.json', record)
            args = SimpleNamespace(**vars(sandbox.args))
            args.gid1 = None
            args.tmobile_wholesale = False
            args.recover = Path('AUTO')
            self.assertEqual(await carrier.execute_with_retry(args, None), 0)
            self.assertNotIn(sandbox.alias, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'undone')

    async def test_automatic_rerun_does_not_trust_an_incomplete_verification_record(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.automatic()
            record = sandbox.record()
            del record['result']['kTwo']['operator']
            carrier.save_json(sandbox.owner() / 'gid-alias.json', record)
            original = dict(sandbox.catalog)
            with self.assertRaises(RuntimeError):
                await sandbox.automatic()
            self.assertEqual(sandbox.catalog, original)
            self.assertEqual(sandbox.alias_commits, 1)

    async def test_batch_all_installs_two_independent_same_plmn_owners_and_reruns_without_writes(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            self.assertEqual(await sandbox.automatic(), 0)
            iccids = [row['IntegratedCircuitCardIdentity'] for row in ROWS]
            aliases = {sandbox.qualified_alias(iccid) for iccid in iccids}
            self.assertEqual(set(sandbox.committed_aliases), aliases)
            self.assertEqual(sandbox.catalog_writes, 2)
            self.assertEqual(len(set(sandbox.owner(iccid) for iccid in iccids)), 2)
            for iccid in iccids:
                self.assertTrue(carrier.gid_healthy(sandbox.record(iccid)))
                self.assertEqual(sandbox.catalog[sandbox.qualified_alias(iccid)], carrier.bundle_link(BUNDLE))
                self.assertEqual(sandbox.row(iccid)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
            before = dict(sandbox.catalog)
            journals = {iccid: (sandbox.owner(iccid) / 'gid-alias.json').read_bytes() for iccid in iccids}
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual({iccid: (sandbox.owner(iccid) / 'gid-alias.json').read_bytes()
                              for iccid in iccids}, journals)
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_sequential_manual_and_automatic_qualified_owners_use_each_identitys_matched_key(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            sandbox.args.gid1_iccid, sandbox.args.sims = True, '1'
            self.assertEqual(await sandbox.apply(), 0)
            first_record = (sandbox.owner(first) / 'gid-alias.json').read_bytes()
            sandbox.baseline_keys[second] = '310240_GID1-7A'
            self.assertEqual(await sandbox.automatic('2'), 0)
            self.assertEqual(sandbox.record(first)['baseline_key'], ALIAS)
            self.assertEqual(sandbox.record(second)['baseline_key'], '310240_GID1-7A')
            self.assertEqual(sandbox.record(second)['baseline']['kOne']['selected'], BUNDLE)
            self.assertEqual(sandbox.record(second)['baseline']['kOne']['operator']['selected'], BUNDLE)
            self.assertEqual((sandbox.owner(first) / 'gid-alias.json').read_bytes(), first_record)
            self.assertEqual(sandbox.alias_commits, 2)
            for iccid in (first, second):
                self.assertEqual(sandbox.catalog[sandbox.qualified_alias(iccid)], carrier.bundle_link(BUNDLE))
                self.assertTrue(carrier.gid_healthy(sandbox.record(iccid)))

    async def test_removing_either_owner_first_preserves_the_other_current_carrier_and_operator(self):
        for order in (('1', '2'), ('2', '1')):
            case = self.root / ''.join(order)
            case.mkdir()
            with self.subTest(order=order), Sandbox(case) as sandbox:
                sandbox.dual_mvno()
                await sandbox.automatic()
                first = ROWS[int(order[0]) - 1]['IntegratedCircuitCardIdentity']
                other = ROWS[int(order[1]) - 1]['IntegratedCircuitCardIdentity']
                other_slot = 'kOne' if order[1] == '1' else 'kTwo'
                other_journal = (sandbox.owner(other) / 'gid-alias.json').read_bytes()
                self.assertEqual(await sandbox.remove(sims=order[0]), 0)
                self.assertNotIn(sandbox.qualified_alias(first), sandbox.catalog)
                self.assertEqual(sandbox.record(first)['state'], 'undone')
                self.assertEqual(sandbox.record(first)['undo_result'][other_slot]['selected'], BUNDLE)
                self.assertEqual(sandbox.record(first)['undo_result'][other_slot]['operator']['selected'], BUNDLE)
                self.assertEqual(sandbox.row(first)['CFBundleIdentifier'], 'com.apple.TMobile_MVNO_US')
                self.assertEqual(sandbox.row(other)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
                self.assertEqual(sandbox.catalog[sandbox.qualified_alias(other)], carrier.bundle_link(BUNDLE))
                self.assertEqual((sandbox.owner(other) / 'gid-alias.json').read_bytes(), other_journal)
                self.assertEqual(await sandbox.remove(sims=order[1]), 0)
                self.assertNotIn(sandbox.qualified_alias(other), sandbox.catalog)
                self.assertEqual(sandbox.record(other)['state'], 'undone')
                self.assertEqual(sandbox.catalog['310240_GID1-54'], sandbox.original['310240_GID1-54'])
                self.assertTrue(all(row['CFBundleIdentifier'] == 'com.apple.TMobile_MVNO_US'
                                    for row in sandbox.rows))

    async def test_all_removal_includes_inactive_owners_without_disturbing_foreign_lines(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            await sandbox.automatic()
            sandbox.rows = [copy.deepcopy(ROWS[0])]
            foreign = dict(sandbox.rows[0])
            self.assertEqual(await sandbox.remove(), 0)
            for row in ROWS:
                iccid = row['IntegratedCircuitCardIdentity']
                self.assertNotIn(sandbox.qualified_alias(iccid), sandbox.catalog)
                self.assertEqual(sandbox.record(iccid)['state'], 'undone')
            self.assertEqual(sandbox.rows, [foreign])
            self.assertEqual(carrier.gid_active(self.root, sandbox.device.udid), [])

    async def test_cleanup_discovers_manual_and_automatic_owners_after_profiles_disappear(self):
        for no_active_sims in (False, True):
            case = self.root / str(no_active_sims)
            case.mkdir()
            with self.subTest(no_active_sims=no_active_sims), Sandbox(case) as sandbox:
                sandbox.dual_mvno()
                sandbox.args.sims, sandbox.args.gid1_iccid = '1', True
                await sandbox.apply()
                await sandbox.automatic('2')
                sandbox.rows = [] if no_active_sims else [copy.deepcopy(ROWS[0])]
                unowned = ALIAS + '_ID-8901000000000000099'
                sandbox.catalog[unowned] = carrier.bundle_link(BUNDLE)
                expected = dict(sandbox.catalog)
                for row in ROWS:
                    expected.pop(sandbox.qualified_alias(row['IntegratedCircuitCardIdentity']))
                expected.pop(unowned)
                sandbox.trigger_error = no_active_sims
                boundary = (patch.object(carrier, 'install_trigger',
                                         side_effect=AssertionError('Unexpected no-SIM trigger'))
                            if no_active_sims else contextlib.nullcontext())
                with boundary:
                    self.assertEqual(await sandbox.cleanup(), 0)
                self.assertEqual(sandbox.catalog, expected)
                self.assertEqual(sandbox.catalog_writes, 5)
                for row in ROWS:
                    record = sandbox.record(row['IntegratedCircuitCardIdentity'])
                    self.assertEqual(record['state'], 'undone')
                    self.assertTrue(record['rollback_verified'])
                    self.assertEqual(record['undo_selection_status'],
                                     'no_active_sims' if no_active_sims else 'verified')
                    if no_active_sims:
                        self.assertFalse(record.get('undo_result'))
                self.assertEqual(carrier.pending(case, sandbox.device.udid), [])
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])
                if no_active_sims:
                    # Cleanup must rely on catalog/Books proof, not demand selection output.
                    self.assertTrue(all(identities for _, identities in sandbox.rescans))
                before = sandbox.catalog_writes
                self.assertEqual(await sandbox.cleanup(), 0)
                self.assertEqual(sandbox.catalog_writes, before)

    async def test_cleanup_removes_an_unqualified_owned_rule_too(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.apply()
            sandbox.rows = []
            self.assertEqual(await sandbox.cleanup(), 0)
            self.assertNotIn(ALIAS, sandbox.catalog)
            self.assertEqual(sandbox.record()['state'], 'undone')
            self.assertEqual(sandbox.record()['undo_selection_status'], 'no_active_sims')
            self.assertEqual(carrier.pending(self.root, sandbox.device.udid), [])

    async def test_batch_install_and_multi_owner_removal_cleanup_previews_do_not_write(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            sandbox.args.status = True
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(sandbox.catalog, sandbox.original)
            sandbox.args.status = False
            await sandbox.automatic()
            before = dict(sandbox.catalog)
            files = {path.relative_to(self.root): path.read_bytes()
                     for path in self.root.rglob('*') if path.is_file()}
            writes = sandbox.catalog_writes
            sandbox.rows = []
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.remove(status=True), 0)
                self.assertEqual(await sandbox.cleanup(status=True), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.catalog_writes, writes)
            self.assertEqual({path.relative_to(self.root): path.read_bytes()
                              for path in self.root.rglob('*') if path.is_file()}, files)

    async def test_owner_follows_identity_across_slot_moves_for_rerun_and_scoped_removal(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            await sandbox.automatic()
            first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            sandbox.row(first)['Slot'], sandbox.row(second)['Slot'] = 'kTwo', 'kOne'
            before = dict(sandbox.catalog)
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic('1'), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(await sandbox.remove(sims='1'), 0)
            self.assertNotIn(sandbox.qualified_alias(second), sandbox.catalog)
            self.assertEqual(sandbox.record(second)['slot'], 'kTwo')
            self.assertEqual(sandbox.record(second)['undo_result']['kOne']['selected'], 'TMobile_MVNO_US.bundle')
            self.assertEqual(sandbox.record(second)['undo_result']['kOne']['operator']['selected'],
                             'TMobile_MVNO_US.bundle')
            self.assertEqual(sandbox.record(second)['undo_result']['kTwo']['selected'], BUNDLE)
            self.assertEqual(sandbox.row(first)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
            self.assertEqual(await sandbox.remove(sims='2'), 0)
            self.assertEqual(sandbox.record(first)['state'], 'undone')

    async def test_slot_removal_prefers_current_identity_and_never_an_active_owners_stale_slot(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            await sandbox.automatic()
            inactive, active = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            sandbox.rows = [sandbox.row(active)]
            sandbox.row(active)['Slot'] = 'kOne'
            before = dict(sandbox.catalog)
            writes = sandbox.catalog_writes
            self.assertEqual(await sandbox.remove(sims='2'), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.catalog_writes, writes)
            self.assertEqual(await sandbox.remove(sims='1'), 0)
            self.assertNotIn(sandbox.qualified_alias(active), sandbox.catalog)
            self.assertIn(sandbox.qualified_alias(inactive), sandbox.catalog)
            self.assertEqual(sandbox.record(inactive)['state'], 'applied_verified')
            # No active owner now matches; the removed profile's saved slot is eligible.
            self.assertEqual(await sandbox.remove(sims='1'), 0)
            self.assertNotIn(sandbox.qualified_alias(inactive), sandbox.catalog)
            self.assertEqual(sandbox.record(inactive)['state'], 'undone')

    async def test_failed_second_owner_rolls_back_only_itself_in_batch_and_sequential_installs(self):
        for batch in (False, True):
            case = self.root / str(batch)
            case.mkdir()
            with self.subTest(batch=batch), Sandbox(case) as sandbox:
                sandbox.dual_mvno()
                first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
                if not batch:
                    await sandbox.automatic('1')
                sandbox.failure_modes[second] = 'operator-signature'
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic('all' if batch else '2')
                self.assertEqual(sandbox.alias_commits, 2)
                self.assertEqual(sandbox.catalog_writes, 3)
                self.assertEqual(sandbox.catalog[sandbox.qualified_alias(first)], carrier.bundle_link(BUNDLE))
                self.assertNotIn(sandbox.qualified_alias(second), sandbox.catalog)
                self.assertTrue(carrier.gid_healthy(sandbox.record(first)))
                self.assertEqual(sandbox.record(second)['state'], 'rolled_back')
                self.assertEqual(sandbox.record(second)['undo_result']['kOne']['operator']['selected'], BUNDLE)
                self.assertEqual(sandbox.row(first)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
                self.assertEqual(sandbox.row(second)['CFBundleIdentifier'], 'com.apple.TMobile_MVNO_US')
                self.assertEqual(sandbox.catalog['later-change'], ('f', b'preserve this current change'))
                self.assertEqual(carrier.gid_pending(case, sandbox.device.udid), [])

    async def test_batch_sim_swap_after_first_verified_owner_stops_later_writes(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            swapped = False

            async def ready(*args):
                nonlocal swapped
                owners = list(self.root.glob('*/gid-alias.json'))
                if not swapped and any(carrier.gid_healthy(carrier.read_json(path)) for path in owners):
                    sandbox.row(second)['IntegratedCircuitCardIdentity'] = '8901000000000000099'
                    swapped = True
                return await sandbox.ready(*args)

            with patch.object(carrier, 'ready_device', AsyncMock(side_effect=ready)):
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic()
            self.assertTrue(swapped)
            self.assertEqual(sandbox.alias_commits, 1)
            self.assertEqual(sandbox.catalog_writes, 1)
            self.assertTrue(carrier.gid_healthy(sandbox.record(first)))
            self.assertIn(sandbox.qualified_alias(first), sandbox.catalog)
            self.assertNotIn(sandbox.qualified_alias(second), sandbox.catalog)

    async def test_failed_install_never_adopts_an_unowned_alias_but_explicit_cleanup_can_remove_wholesale(self):
        for identical in (False, True):
            case = self.root / str(identical)
            case.mkdir()
            with self.subTest(identical=identical), Sandbox(case) as sandbox:
                unowned = carrier.bundle_link(BUNDLE) if identical else ('l', b'conflicting alias')
                sandbox.catalog[QUALIFIED_ALIAS] = unowned
                before = dict(sandbox.catalog)
                sandbox.args.gid1_iccid = True
                with self.assertRaises(RuntimeError):
                    await sandbox.apply()
                self.assertEqual(sandbox.catalog, before)
                self.assertFalse(sandbox.record()['ownership_established'])
                self.assertEqual(sandbox.alias_commits, 0)
                self.assertEqual(await sandbox.cleanup(), 0)
                expected = dict(before)
                if identical:
                    expected.pop(QUALIFIED_ALIAS)
                self.assertEqual(sandbox.catalog, expected)
                self.assertEqual(sandbox.catalog_writes, int(identical))
                self.assertFalse(sandbox.record()['ownership_established'])

    async def test_changed_owned_alias_is_not_deleted_by_all_cleanup(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            await sandbox.automatic()
            second = ROWS[1]['IntegratedCircuitCardIdentity']
            sandbox.catalog[sandbox.qualified_alias(second)] = ('l', b'changed by somebody else')
            before = dict(sandbox.catalog)
            with self.assertRaises(RuntimeError):
                await sandbox.cleanup()
            self.assertEqual(sandbox.catalog, before)

    async def test_unqualified_or_unfinished_owner_blocks_a_new_distinct_owner(self):
        for unfinished in (False, True):
            case = self.root / str(unfinished)
            case.mkdir()
            with self.subTest(unfinished=unfinished), Sandbox(case) as sandbox:
                sandbox.dual_mvno()
                first = ROWS[0]['IntegratedCircuitCardIdentity']
                if unfinished:
                    await sandbox.automatic('1')
                    record = sandbox.record(first)
                    record.update(state='apply_started', selection_verified=False)
                    carrier.save_json(sandbox.owner(first) / 'gid-alias.json', record)
                else:
                    # The unqualified rule was installed before the second same-PLMN line appeared.
                    sandbox.rows = sandbox.rows[:1]
                    sandbox.args.sims = '1'
                    await sandbox.apply()
                    sandbox.rows.append(copy.deepcopy(ROWS[1]))
                before = dict(sandbox.catalog)
                writes = sandbox.catalog_writes
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic('2')
                self.assertEqual(sandbox.catalog, before)
                self.assertEqual(sandbox.catalog_writes, writes)

    async def test_generic_recovery_preserves_multiple_healthy_owners_and_removes_only_interrupted_one(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            await sandbox.automatic()
            first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            args = SimpleNamespace(**vars(sandbox.args))
            args.gid1, args.tmobile_wholesale, args.recover = None, False, Path('AUTO')
            before = dict(sandbox.catalog)
            writes = sandbox.catalog_writes
            self.assertEqual(await carrier.execute_with_retry(args, None), 0)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.catalog_writes, writes)
            first_journal = (sandbox.owner(first) / 'gid-alias.json').read_bytes()
            record = sandbox.record(second)
            record.update(state='apply_started', selection_verified=False)
            carrier.save_json(sandbox.owner(second) / 'gid-alias.json', record)
            self.assertEqual(await carrier.execute_with_retry(args, None), 0)
            self.assertEqual((sandbox.owner(first) / 'gid-alias.json').read_bytes(), first_journal)
            self.assertIn(sandbox.qualified_alias(first), sandbox.catalog)
            self.assertNotIn(sandbox.qualified_alias(second), sandbox.catalog)
            self.assertEqual(sandbox.record(second)['state'], 'undone')
            self.assertEqual(sandbox.record(second)['undo_result']['kOne']['operator']['selected'], BUNDLE)
            self.assertEqual(sandbox.row(first)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
            self.assertEqual(carrier.gid_pending(self.root, sandbox.device.udid), [])

    async def test_duplicate_identity_or_ownership_blocks_new_writes(self):
        for duplicate_owner in (False, True):
            case = self.root / str(duplicate_owner)
            case.mkdir()
            with self.subTest(duplicate_owner=duplicate_owner), Sandbox(case) as sandbox:
                sandbox.dual_mvno()
                if duplicate_owner:
                    first = ROWS[0]['IntegratedCircuitCardIdentity']
                    await sandbox.automatic('1')
                    shutil.copytree(sandbox.owner(first), case / 'duplicate-owner')
                else:
                    for key in ('InternationalMobileSubscriberIdentity', 'IntegratedCircuitCardIdentity'):
                        sandbox.rows[1][key] = sandbox.rows[0][key]
                before = dict(sandbox.catalog)
                writes = sandbox.catalog_writes
                with self.assertRaises(RuntimeError):
                    await sandbox.automatic('2')
                self.assertEqual(sandbox.catalog, before)
                self.assertEqual(sandbox.catalog_writes, writes)

    async def test_second_owner_disconnect_reconnects_without_replaying_either_catalog_write(self):
        with Sandbox(self.root) as sandbox:
            sandbox.dual_mvno()
            first, second = (row['IntegratedCircuitCardIdentity'] for row in ROWS)
            await sandbox.automatic('1')
            first_journal = (sandbox.owner(first) / 'gid-alias.json').read_bytes()
            sandbox.failure_modes[second] = 'drop'
            self.assertEqual(await sandbox.automatic('2'), 0)
            self.assertEqual(sandbox.catalog_writes, 2)
            self.assertEqual(sandbox.alias_commits, 2)
            self.assertEqual((sandbox.owner(first) / 'gid-alias.json').read_bytes(), first_journal)
            self.assertTrue(sandbox.record(second)['verification_connection_errors'])
            for iccid in (first, second):
                self.assertTrue(carrier.gid_healthy(sandbox.record(iccid)))
                self.assertIn(sandbox.qualified_alias(iccid), sandbox.catalog)
                self.assertEqual(sandbox.row(iccid)['CFBundleIdentifier'], 'com.apple.TMobile_Wholesale_US')
            self.assertTrue(all(client.closed for client in sandbox.clients))

    async def test_cleanup_is_usb_only_even_for_preview(self):
        with Sandbox(self.root) as sandbox:
            await sandbox.automatic()
            before = dict(sandbox.catalog)
            writes = sandbox.catalog_writes
            with patch.object(carrier, 'CONNECTION', 'WiFi'):
                for status in (False, True):
                    with self.subTest(status=status), self.assertRaises(RuntimeError):
                        await sandbox.cleanup(status=status)
            self.assertEqual(sandbox.catalog, before)
            self.assertEqual(sandbox.catalog_writes, writes)


if __name__ == '__main__':
    unittest.main()
