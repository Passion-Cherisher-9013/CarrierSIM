#!/usr/bin/env python3
"""Local environment and launcher menu. This module does not modify the phone itself."""
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from carriersim_version import VERSION

ROOT = Path(__file__).resolve().parent
UNCONFIRMED = 3  # carrier.py: written, but iOS did not confirm the chosen bundle
NOTHING_TO_WRITE = 4  # carrier.py --status with CARRIERSIM_PLAN=1: the plan writes nothing


def run_carrier(python, args, **kwargs):
    # Ctrl+C reaches carrier.py too. The launcher must not kill it 0.25 s later (what
    # subprocess.run does on KeyboardInterrupt): carrier.py needs time to put Books back
    # and write its journal. So the launcher swallows SIGINT while carrier.py runs. A no-op
    # handler, not SIG_IGN: an ignored SIGINT would be inherited and carrier.py could not be stopped.
    previous = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        command = [str(python), '-u', str(ROOT / 'carrier.py')]
        return subprocess.run([*command, *args], cwd=ROOT, **kwargs).returncode
    finally:
        signal.signal(signal.SIGINT, previous)


def check_writable():
    # .venv and runs (backups, journals) are created next to the script.
    try:
        with tempfile.NamedTemporaryFile(dir=ROOT, prefix='.write-test-'):
            pass
    except OSError:
        raise RuntimeError(f'The script cannot run from this folder: {ROOT}\n'
                           'Close this window, copy the entire CarrierSIM folder '
                           'to Downloads, and launch it there.') from None


# pyimg4 (pulled in by pymobiledevice3) requires these compressors, but CarrierSIM never
# imports pyimg4. They ship wheels only up to Python 3.12, so on 3.13+ pip would need a
# C compiler. Empty placeholder distributions satisfy the requirement instead.
PLACEHOLDERS = (('pylzss', '0.3.4'), ('lzfse', '0.4.2'))


def placeholder_wheel(directory, name, version):
    info = f'{name}-{version}.dist-info'
    files = {f'{info}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
                                 'Summary: CarrierSIM placeholder; the real package is not used\n',
             f'{info}/WHEEL': 'Wheel-Version: 1.0\nGenerator: carriersim\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    record = ''.join(f'{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d.encode()).digest()).rstrip(b"=").decode()},'
                     f'{len(d.encode())}\n' for n, d in files.items()) + f'{info}/RECORD,,\n'
    path = Path(directory) / f'{name}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for n, d in files.items():
            archive.writestr(n, d)
        archive.writestr(f'{info}/RECORD', record)
    return str(path)


def install_dependencies(python, version):
    # Prefer an older release with a wheel over building a newer one from source.
    pip = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary']
    if version >= (3, 13):
        with tempfile.TemporaryDirectory() as directory:
            wheels = [placeholder_wheel(directory, n, v) for n, v in PLACEHOLDERS]
            if subprocess.run(pip + ['--no-deps', '--no-index', *wheels]).returncode:
                return False
    return not subprocess.run(pip + ['-r', str(ROOT / 'requirements.txt')]).returncode


def python_environment():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required: https://www.python.org/downloads/')
    if sys.platform == 'win32' and sys.maxsize <= 2**32:
        raise RuntimeError('Windows requires Python x64.')
    env = ROOT / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    current = '%d.%d' % sys.version_info[:2]
    for attempt in range(2):
        if not env.exists():
            print('Creating the local .venv environment…', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
        if not python.is_file():
            raise RuntimeError('The .venv folder is incomplete or belongs to another OS. Rename it and launch again.')
        probe = subprocess.run([str(python), '-c',
            'import sys; assert sys.version_info >= (3,11); '
            'assert sys.platform != "win32" or sys.maxsize > 2**32; '
            'print("%d.%d" % sys.version_info[:2])'], capture_output=True, text=True)
        if probe.returncode:
            raise RuntimeError('Python in .venv is unsuitable. Rename the .venv folder and launch again.')
        version = probe.stdout.strip()
        dependencies = subprocess.run([str(python), '-c',
            'from importlib.metadata import version; '
            'assert version("pymobiledevice3") == "11.12.5"; '
            'from pymobiledevice3.services.afc import AfcService; '
            'from pymobiledevice3.services.installation_proxy import InstallationProxyService'], capture_output=True)
        if not dependencies.returncode:
            return python
        if version == current or attempt:
            break
        # A half-installed .venv from another Python (e.g. a failed 3.14 attempt) is rebuilt
        # with the interpreter the launcher picked; a working .venv is never touched.
        print(f'Recreating .venv: Python {version} → {current}…', flush=True)
        shutil.rmtree(env)
    print('Installing dependencies in .venv. Internet access is required…', flush=True)
    if not install_dependencies(python, tuple(map(int, version.split('.')))):
        if tuple(map(int, version.split('.'))) >= (3, 13):
            raise RuntimeError(f'Could not install dependencies for Python {version}.\n'
                               'Install Python 3.12 from python.org '
                               '(on Windows, use the Windows installer (64-bit) for 3.12.10). '
                               'You do not need to remove your old Python. Then launch the script again.')
        raise RuntimeError('Could not install dependencies. Check your internet connection and launch again.')
    return python


def startup_banner():
    print('\n  CarrierSIM · ' + VERSION)
    print('  Carrier profiles · iPhone / iPad')
    print('  VoWiFi / 5G / EVS  · Russia / Belarus')
    print('\n  Author: Vladimir B / vlw · vlwwwwww@gmail.com', flush=True)


def menu(wifi=False):
    title = f'CarrierSIM · {VERSION}'
    connection = 'Wi-Fi · experimental' if wifi else 'USB · cable'
    print('\n  ' + '─' * 50)
    print(f'  {title:<38}{connection}')
    print('  https://github.com/ios-bundles/CarrierSIM')
    print('  ' + '─' * 50)
    print('\n  PROFILES\n'
          '   1  Install profiles from bundle.yaml\n'
          '   7  Choose another profile and SIM\n'
          '   4  Restore stock profile · IMSI only\n'
          '   5  Recover after a failure\n'
          '  12  Experimental GID1 override · USB only\n'
          '  13  Undo GID1 override using run directory · USB only\n'
          '  14  Automatic T-Mobile Wholesale installation · USB only\n'
          '  15  Remove T-Mobile Wholesale rules · USB only\n'
          '  16  Clean up owned overrides and recognizable Wholesale rules · USB only\n\n'
          '  CHECKS AND REPORTS · read-only\n'
          '   2  View SIMs and installation plan\n'
          '   3  Check computer and files\n'
          '   8  Connection diagnostics · IMS, VoWiFi, ePDG, 5G\n'
          '   9  Watch call · channel and codec\n'
          '  11  Profile report\n\n'
          '  SETTINGS AND HELP\n'
          '  10  Switch USB / Wi-Fi\n'
          '   6  Help\n'
          '   0  Exit\n')
    while True:
        choice = input('  Your choice: ').strip()
        if choice == '0': return None
        if choice == '1': return []
        if choice == '2': return ['--status']
        if choice == '3': return ['--check']
        if choice == '6': return ['--help']
        if choice == '4': return ['--restore', '--sims', choose_sims('Which SIMs should use the stock profile?')]
        if choice == '5': return ['--recover']
        if choice == '7': return other_profile()
        if choice == '8': return ['--diagnose']
        if choice == '11': return ['--report']
        if choice == '9': return ['--watch-call']
        if choice == '10': return 'wifi'
        if choice in ('12', '13'):
            if wifi:
                print('  GID1 apply and undo require USB. Switch to USB with menu 10 first.')
                continue
            return gid_override() if choice == '12' else undo_gid_override()
        if choice in ('14', '15', '16'):
            if wifi:
                print('  Wholesale installation, removal and override cleanup require USB. Switch with menu 10 first.')
                continue
            if choice == '16': return cleanup_overrides()
            return wholesale_profile(remove=choice == '15')
        print('Enter a number from 0 to 16. Installation has not started.')


def other_profile():
    print('\n  Enter the system carrier bundle name from /System/Library/Carrier Bundles/iPhone.\n'
          '  Examples: O2_Germany, Swisscom_ch, Vodafone_hu. Names are case-sensitive.\n'
          '  This overrides bundle.yaml for the selected SIMs. Leave blank to return to the menu.')
    while True:
        name = input('  Profile: ').strip().removesuffix('.bundle')
        if not name: return False
        if re.fullmatch(r'[A-Za-z0-9_]+', name): break
        print('  Use only Latin letters, digits and _. Example: O2_Germany.')
    # A tuple asks main() to show the plan and the bundle passport first and write only after a yes.
    return ('confirm', ['--bundle', name, '--sims', choose_sims('Which SIMs should use this profile?')])


def ask_yes(question, default_yes=True):
    while True:
        answer = input(question).strip().lower()
        if not answer: return default_yes
        if answer in ('y', 'yes'): return True
        if answer in ('n', 'no'): return False
        print('  Enter y/yes or n/no.')


def choose_sims(question):
    print(f'\n  {question}\n'
          '  1  SIM 1\n'
          '  2  SIM 2\n'
          '  Enter — both. Installation skips unconfigured foreign SIMs unless their slot is selected.')
    while True:
        sims = input('  SIM: ').strip()
        if sims in ('', '1', '2'): return sims or 'all'
        print('  Enter 1, 2, or press Enter.')


def gid_override():
    print('\n  Experimental user-catalog GID1 alias. System bundles remain untouched.\n'
          '  Selecting one SIM does not isolate a GID rule from other SIMs.\n'
          '  The first service/reboot trial must end with undo. Blank input cancels.')
    while True:
        gid = input('  GID1 (hex, 1–16 bytes): ').strip()
        if not gid: return False
        if re.fullmatch(r'[0-9A-Fa-f]{2,32}', gid) and len(gid) % 2 == 0: break
        print('  Enter 2–32 hex digits, with an even length.')
    while True:
        name = input('  Explicit system bundle name: ').strip().removesuffix('.bundle')
        if not name: return False
        if re.fullmatch(r'[A-Za-z0-9_]+', name): break
        print('  Use only Latin letters, digits and _.')
    while True:
        slot = input('  Exactly one SIM slot (1 or 2; blank cancels): ').strip()
        if not slot: return False
        if slot in ('1', '2'): break
        print('  Enter 1 or 2.')
    return ('gid-confirm', ['--gid1', gid.upper(), '--bundle', name, '--sims', slot])


def undo_gid_override():
    directory = input('\n  GID1 operation run directory (blank cancels): ').strip()
    if not directory: return False
    return ('gid-confirm', ['--undo-gid1', directory])

def wholesale_profile(remove=False):
    print('\n  Remove T-Mobile Wholesale rules, including disabled or removed eSIMs.' if remove else
          '\n  Install T-Mobile Wholesale for all eligible active SIMs, or choose one slot.')
    print('  Enter or all includes recognizable full-ICCID Wholesale rules from any computer.\n'
          '  Choosing 1 or 2 removes only locally journaled installations for that slot.\n'
          '  Unknown phone rules are discovered and backed up only after confirmation;\n'
          '  discovery uses native staging and is not read-only. Other rules stay untouched.' if remove else
          '  Enter or all selects all eligible SIMs.')
    print('  A read-only preview runs before confirmation. No identifiers or run directory are needed.')
    while True:
        slot = input('  SIM selection (Enter/all, 1 or 2): ').strip().lower()
        if slot in ('', 'all', '1', '2'): break
        print('  Enter 1, 2, all, or press Enter for all.')
    args = ['--remove-tmobile-wholesale' if remove else '--tmobile-wholesale',
            '--sims', slot or 'all']
    return ('wholesale-confirm', args)


def cleanup_overrides():
    print('\n  Remove locally owned GID1/ICCID overrides and recognizable full-ICCID Wholesale rules,\n'
          '  including rules from another computer and disabled or removed eSIMs.\n'
          '  Other GID1 cleanup still needs local journals; unsupported rules stay untouched.\n'
          '  No SIM or run directory selection is needed. The preview is read-only.\n'
          '  Phone-side discovery and backup happen only after confirmation, using native staging.\n'
          '  Only y/yes confirms cleanup. Enter cancels.')
    return ('cleanup-confirm', ['--cleanup-overrides'])



def show_help():
    # Parsing help exits before dependency imports or device discovery in carrier.py.
    return run_carrier(Path(sys.executable), ['--help'])


def main():
    os.chdir(ROOT)
    if sys.argv[1:] in (['--help'], ['-h']):
        return show_help()
    check_writable()
    python = None
    if len(sys.argv) > 1:
        python = python_environment()
        return run_carrier(python, sys.argv[1:])
    startup_banner()
    wifi = False
    while True:
        args = menu(wifi)
        if args is None: return 0
        if args is False: continue
        confirm = isinstance(args, tuple)
        gid_confirm = confirm and args[0] == 'gid-confirm'
        wholesale_confirm = confirm and args[0] == 'wholesale-confirm'
        cleanup_confirm = confirm and args[0] == 'cleanup-confirm'
        usb_confirm = gid_confirm or wholesale_confirm or cleanup_confirm
        if confirm: args = args[1]
        if args == 'wifi':
            wifi = not wifi
            if wifi:
                print('\n  Wi-Fi mode: ordinary device actions use wireless transport.\n'
                      '  First pair over USB and enable “Show this iPhone when on Wi-Fi”\n'
                      '  in Finder/iTunes. Use the same network as the computer.\n'
                      '  Start with menu 2, then 1. See the README section about --wifi.\n'
                      '  GID1, Wholesale installation/removal and override cleanup always require USB.')
            continue
        if usb_confirm and wifi:
            print('This operation requires USB. Switch to USB with menu 10 first.')
            continue
        if args == ['--help']:
            show_help()
            input('\nPress Enter to return to the main menu…')
            continue
        if wifi and args != ['--check'] and not usb_confirm: args = [*args, '--wifi']
        print('\n' + '─' * 56, flush=True)
        try:
            if python is None: python = python_environment()
            env = {**os.environ, 'CARRIERSIM_MENU': '1'}
            code = run_carrier(python, [*args, '--status'], env={**env, 'CARRIERSIM_PLAN': '1'}) if confirm else 0
            if code == NOTHING_TO_WRITE:
                print('\n  Nothing to write; installation was not started. See the reason above.'); code = 0
            elif confirm and not code and not ask_yes(
                    '\n  Confirm override and Wholesale cleanup? y/yes confirms; Enter cancels [NO]: '
                    if cleanup_confirm else
                    '\n  Confirm this T-Mobile Wholesale installation/removal? y/yes confirms; Enter cancels [NO]: '
                    if wholesale_confirm else
                    '\n  Confirm this GID1 apply/undo operation? y/yes confirms; Enter cancels [NO]: ' if gid_confirm
                    else '\n  Write this profile? Enter or y — yes, n — no: ',
                    default_yes=not usb_confirm):
                print('Operation cancelled; no write was started.')
            elif not code:
                code = run_carrier(python, args, env=env)
            if code == UNCONFIRMED:
                print('\nProfile selection was not confirmed. See the operation journal.')
            elif code:
                print('\nThe action did not complete. See the reason above.')
        except Exception as error:
            print('\nLaunch error:', str(error).strip() or type(error).__name__)
        input('\nPress Enter to return to the main menu…')


if __name__ == '__main__':
    try: sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print('\nLaunch interrupted. If writing had started, keep runs and use recovery.')
        sys.exit(130)
    except Exception as error:
        print('Launch error:', str(error).strip() or type(error).__name__, file=sys.stderr)
        sys.exit(1)
