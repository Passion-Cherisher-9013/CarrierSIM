"""Journal ownership, selection proof and recovery regressions; no phone connections."""
import contextlib
import copy
import io
from pathlib import Path
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
                                    tmobile_wholesale=False, remove_tmobile_wholesale=False,
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

    @property
    def alias(self):
        base = '310240_GID1-' + self.args.gid1 if self.args.gid1 else self.baseline_key
        qualified = self.args.gid1_iccid or self.args.tmobile_wholesale
        return base + '_ID-' + ROWS[1]['IntegratedCircuitCardIdentity'] if qualified else base

    def trigger(self, args, assets, work, info, identities, bundle):
        if self.trigger_error:
            raise RuntimeError('No validated trigger in this sandbox')
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
            if self.alias in payload and self.alias not in original:
                self.alias_commits += 1
        journal.update(phase='placement-observed', complete=True, requires_recovery=False, books_restored=True)
        carrier.save_json(run / 'journal.json', journal)
        return original

    async def rescan(self, device, trigger, stage):
        self.connected(device)
        selected = BUNDLE if self.alias in self.catalog else 'TMobile_MVNO_US.bundle'
        verification, other = 'Success', 'Verizon_LTE_US.bundle'
        if stage.name == 'rescan':
            if not self.mode.startswith('drop'):
                self.catalog['later-change'] = ('f', b'preserve this current change')
            if self.mode == 'interrupt':
                raise KeyboardInterrupt()
            if self.mode == 'missing':
                return {'ipcc_installation_completed': True, 'log_error': 'stream lost'}
            if self.mode == 'reject':
                selected = 'TMobile_MVNO_US.bundle'
            if self.mode == 'signature':
                verification = 'Failed'
            if self.mode == 'other-slot':
                other = 'WrongOther.bundle'
        if stage.name == 'rescan-reconnected':
            if self.mode == 'drop-signature':
                verification = 'Failed'
            if self.mode == 'drop-reject':
                selected = 'TMobile_MVNO_US.bundle'
            if self.mode == 'drop-other-slot':
                other = 'WrongOther.bundle'
        operator, operator_verification, other_operator = selected, verification, other
        if stage.name == 'rescan':
            if self.mode == 'operator-reject':
                operator = 'TMobile_MVNO_US.bundle'
            if self.mode == 'operator-signature':
                operator_verification = 'Failed'
            if self.mode == 'operator-other-slot':
                other_operator = 'WrongOther.bundle'
        text = ''
        for row in self.rows:
            index = 1 if row['Slot'] == 'kOne' else 2
            tmobile = row['MCC'] + row['MNC'] == '310240'
            bundle, op = (selected, operator) if tmobile else (other, other_operator)
            checked, op_checked = (verification, operator_verification) if tmobile else ('Success', 'Success')
            text += (baseline_log(bundle, self.baseline_key, index) if tmobile and stage.name == 'baseline' else
                     bundle_block(index, bundle, checked))
            if self.mode != 'operator-missing' or stage.name != 'rescan':
                text += bundle_block(index, op, op_checked, kind='Operator')
            row['CFBundleIdentifier'] = 'com.apple.' + bundle.removesuffix('.bundle')
        if not self.rows:
            text = 'CommCenter: Received command at the server: kMatchBundle\n'
        (stage / 'commcenter.log').write_text(text, encoding='utf-8')
        dropping = ((stage.name == 'rescan' and self.mode.startswith('drop') and self.mode != 'drop-undo') or
                    (stage.name == 'undo-rescan' and self.mode == 'drop-undo') or
                    (stage.name == 'rescan-reconnected' and self.mode == 'drop-twice'))
        if dropping:
            if self.mode == 'drop-alias':
                self.catalog[self.alias] = ('l', b'not our alias any more')
            if self.mode == 'drop-catalog':
                self.catalog['later-change'] = ('f', b'preserve this current change')
            if self.mode == 'drop-sim':
                self.rows[1]['IntegratedCircuitCardIdentity'] = '8901000000000000099'
            if self.mode == 'drop-device':
                self.wrong_reconnect = True
            device.disconnected = True
            return {'ipcc_installation_completed': True, 'log_error': 'ConnectionTerminatedError',
                    'log_interrupted': True}
        return {'ipcc_installation_completed': True}

    def owner(self):
        return next(self.root.glob('*/gid-alias.json')).parent

    def record(self):
        return carrier.read_json(self.owner() / 'gid-alias.json')

    async def apply(self):
        return await carrier.execute_with_retry(self.args, None)

    async def undo(self):
        args = SimpleNamespace(**vars(self.args))
        args.gid1, args.undo_gid1 = None, self.owner()
        args.gid1_iccid = False
        args.tmobile_wholesale = args.remove_tmobile_wholesale = False
        return await carrier.execute_with_retry(args, None)

    async def automatic(self):
        self.args.gid1 = None
        self.args.gid1_iccid = False
        self.args.tmobile_wholesale = True
        self.args.sims = 'all'
        return await self.apply()

    async def remove(self, status=False):
        args = SimpleNamespace(**vars(self.args))
        args.gid1, args.undo_gid1 = None, None
        args.gid1_iccid = args.tmobile_wholesale = False
        args.remove_tmobile_wholesale, args.status = True, status
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

    async def test_automatic_preview_never_exports_catalog_or_installs_trigger(self):
        with Sandbox(self.root) as sandbox:
            sandbox.args.status = True
            with patch.object(carrier, 'transfer', side_effect=AssertionError('Unexpected catalog operation')), \
                    patch.object(carrier, 'install_trigger', side_effect=AssertionError('Unexpected trigger')):
                self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(sandbox.catalog, sandbox.original)
            self.assertEqual(list(self.root.iterdir()), [])

    async def test_automatic_unsupported_and_ambiguous_sims_fail_without_changes(self):
        for kind in ('foreign', 'ambiguous', 'explicit-foreign', 'inactive'):
            case = self.root / kind
            case.mkdir()
            with self.subTest(kind=kind), Sandbox(case) as sandbox:
                sandbox.args.gid1 = None
                sandbox.args.tmobile_wholesale = True
                sandbox.args.sims = 'all'
                if kind == 'foreign':
                    sandbox.rows = sandbox.rows[:1]
                elif kind == 'ambiguous':
                    sandbox.rows[0]['CFBundleIdentifier'] = 'com.apple.TMobile_MVNO_US'
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

    async def test_automatic_stock_wholesale_is_an_unowned_noop_and_cannot_be_removed(self):
        with Sandbox(self.root) as sandbox:
            sandbox.rows[1]['CFBundleIdentifier'] = 'com.apple.TMobile_Wholesale_US'
            sandbox.catalog[QUALIFIED_ALIAS] = carrier.bundle_link(BUNDLE)
            original = dict(sandbox.catalog)
            self.assertEqual(await sandbox.automatic(), 0)
            self.assertEqual(await sandbox.remove(), 0)
            sandbox.args.status = True
            with patch.dict(carrier.os.environ, CARRIERSIM_PLAN='1'):
                self.assertEqual(await sandbox.remove(status=True), carrier.NOTHING_TO_WRITE)
            self.assertEqual(sandbox.catalog, original)
            self.assertEqual(list(self.root.iterdir()), [])

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


if __name__ == '__main__':
    unittest.main()
