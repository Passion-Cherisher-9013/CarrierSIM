CarrierSIM
Carrier profiles · iPhone / iPad
VoWiFi / 5G / EVS · Russia / Belarus

Discussion on 4PDA:
https://4pda.to/forum/index.php?showtopic=1055886&view=findpost&p=145261196

Python script for macOS, Windows and Linux. Each selected SIM is associated,
by its full IMSI, with an unchanged system carrier bundle: Vodafone_hu by
default, or Vodafone_ro for MTS Russia (bundle.yaml). Without flags, eligible
Russian and Belarusian SIMs reported by the iPhone are selected; a foreign
SIM is changed only by an explicit configuration entry or slot selection.
An invalid IMSI on a selected SIM stops the operation before writing.
The target bundle is already in /System/Library/Carrier Bundles/iPhone on
the phone and is not modified. Normal operations change IMSI symlinks in
the user catalog only. assets.zip contains signed IPCC rescan triggers.
The automatic T-Mobile Wholesale workflow detects eligible SIMs (including
eSIMs) currently reporting TMobile_MVNO_US. Manual GID1 remains available
for expert use; both workflows are described below.

iPad Wi-Fi + Cellular requires an active SIM or eSIM. Installation has not
yet been tested on a device. Wi-Fi-only models cannot install profiles.
Family-specific bundle/catalog paths are used for cellular iPads.

PURPOSE
Designed to enable VoWiFi (Wi-Fi Calling) in Russia. Changing the bundle
also enables EVS and 5G on some bands; see EVS and 5G below. Results depend
on the carrier, plan, network, model and iOS version. Installation alone
does not test the actual call codec or radio band. Use diagnostics and
real service checks rather than assuming that a switch proves operation.

PREPARATION
1. Extract the entire archive into a writable folder such as Downloads.
2. Install Python 3.11 or later; 3.12 is recommended. Windows requires x64.
3. Windows needs Apple's services and libraries from desktop iTunes x64:
   https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362
   A 4PDA download returning 404 may require registration, login and some
   forum activity. Apple Devices and Microsoft Store iTunes are not found
   automatically; library directories can be provided with --apple-dir.
   macOS uses system libraries; no additional installation is required.
   Linux needs system usbmuxd; Apple's AirTrafficHost library is not needed.
4. Connect USB, enable the relevant lines, unlock and confirm Trust.
   Finish Finder/iTunes synchronization first. USB is needed for initial
   Trust; normal IMSI operations can later use Wi-Fi (see WITHOUT A CABLE).
   GID1 and automatic Wholesale installation/removal are always USB-only.
   Keep the phone unlocked until completion: when iCloud Backup is enabled,
   a charging phone (including a computer cable) with a dark screen starts
   a backup roughly once a day, rewriting Books support files mid-stage.
   The script warns before stages about this risk.

If the device is not detected, unlock it, allow connections in Settings →
Privacy & Security → Wired Accessories and reconnect. On Linux, if needed:
sudo systemctl restart usbmuxd

WITHOUT PYTHON: STANDALONE BUILDS
Releases include CarrierSIM-vN-Linux-x64.zip, built on Ubuntu 22.04.
Install system usbmuxd, extract with unzip to preserve symlinks, then run
./CarrierSIM in a terminal. Older systems should use the Python version:
CarrierSIM-vN-Python-universal.zip (macOS, Windows, Linux).
Other standalone archives are CarrierSIM-vN-macOS-arm64.zip,
CarrierSIM-vN-macOS-x86_64.zip and CarrierSIM-vN-Windows-x64.zip. Python and
dependencies are included; installing Python is unnecessary. Windows still
needs iTunes x64 as described above.
GitHub Actions builds these archives from the same tag. Verify provenance:
gh attestation verify FILE.zip -R ios-bundles/CarrierSIM
Extract everything: bundle.yaml, assets.zip and README must be beside
CarrierSIM (CarrierSIM.exe); runs is created there.
macOS builds are unsigned. Remove quarantine from the extracted folder
once with xattr -dr com.apple.quarantine CARRIER_SIM_FOLDER, then double-click
CarrierSIM. Menus and flags are the same as in the script.
The macOS arm64 build was checked without a phone. Windows and Intel Mac
builds have not been run on physical hardware.

QUICK START
macOS: Launch macOS.command
Windows: Launch Windows.cmd
Linux: bash "Launch Linux.sh"
Menu:
  1  Install using bundle.yaml (MTS Russia: Vodafone_ro; others: Vodafone_hu)
  2  Show SIMs and the installation plan, without writing
  3  Check the computer and files, without a phone
  4  Restore normal selection for one/all SIMs (remove IMSI symlinks only)
  5  Recover after failure
  6  Show flag help
  7  Choose another bundle and SIM; preview and bundle details, then confirm
  8  Connection diagnostics: IMS, VoLTE/VoWiFi/VoNR, ePDG, network, 5G SA
  9  Watch a test call: Wi-Fi/LTE/5G transport and codec
  10 Switch normal phone operations between USB and Wi-Fi
  11 Profile report for the forum or an issue (read-only)
  12 Experimental GID1 override (preview, explicit yes, USB-only)
  13 Undo GID1 override using its run directory (preview, explicit yes, USB-only)
  14 Automatically install T-Mobile Wholesale; no manual identifiers, USB-only
  15 Remove an owned qualified Wholesale installation; explicit yes, USB-only
  0  Exit
Press Enter after each action to return to the menu.
If the directory is not writable, the launcher reports it immediately.
Items 12-15 default to NO at confirmation: Enter cancels. Item 7 keeps blank=yes.
Items 12-15 refuse Wi-Fi mode; use item 10 to select USB first.
For items 14/15, Enter at the earlier SLOT prompt means automatic selection.
An already-installed/no-op preview skips confirmation and writing.

At first launch, dependencies are installed into .venv beside the script
(internet required). System Python is not modified. On macOS, the launcher
prefers 3.12 or 3.11. Windows tries py, then python, then standard install
locations. Linux prefers 3.12 or 3.11, then other available python3 versions.
Python 3.11 or later is required (Windows x64). On Python 3.13+, empty stubs
replace pylzss and lzfse: these versions have no prebuilt wheels and the
script does not need them. A C++ compiler is unnecessary.
Launchers accept carrier.py flags, for example:
"Launch Windows.cmd" --status
./"Launch macOS.command" --check
bash "Launch Linux.sh" --check
With flags, one command runs without a menu.

AUTOMATIC T-MOBILE WHOLESALE (items 14 and 15)
Use the updated project folder or a release containing these changes.
Previously downloaded binaries do not gain new menus from Python edits.
Choose 14, leave the slot blank for automatic selection, inspect the plan,
then type y/yes to install. Enter at confirmation cancels. Specify 1 or 2
if more than one eligible SIM is present. Unsupported selections stop safely.
Choose 15 to preview and explicitly remove an owned qualified installation.

Command line (py instead of python3 on Windows):
python3 launch.py --tmobile-wholesale --status
python3 launch.py --tmobile-wholesale
python3 launch.py --remove-tmobile-wholesale --status
python3 launch.py --remove-tmobile-wholesale
Add --sims 1 or --sims 2 when needed. Standalone builds use CarrierSIM or
CarrierSIM.exe instead of python3 launch.py. Commands without --status are
write actions, not interactive confirmation prompts. USB is required.

Eligibility: the device reports com.apple.TMobile_MVNO_US for the selected
SIM. Other carriers are not converted. An already reported Wholesale profile
is a no-op. A fresh verified stock carrier AND operator baseline must expose
the actual chosen plain PLMN_GID1-HEX key. This is the matched key/prefix,
NOT a raw EF GID1 read; 69 is never assumed. Missing, composite or ambiguous
keys stop before writing. The exact key is qualified by this SIM's full
ICCID and points to the untouched system TMobile_Wholesale_US.bundle.
Another active SIM on the same PLMN is conservatively refused.

Catalog/Books backups, phone and SIM identity bindings, exact write/readback,
and complete carrier/operator verification are required. All other enabled
slots must remain at their baselines. After completed catalog placement,
a connection loss during readback OR the fresh rescan reconnects to the
same phone. Repair an interrupted readback stage, then require an exact
catalog/identity match and complete fresh carrier/operator verification.
Never reapply the alias, repeat removal or accept partial logs. Genuine
rejection still performs exact owned-entry rollback, preserving unrelated
changes. AFC response futures are observed when the pinned SDK releases
them; the original transport error is preserved without orphan warnings.

A verified installation stays applied; no background program or connected
computer is required afterward. The user reported the retained qualified
install working and persisting through reboot on iPhone 18 Pro / iOS 27.0.1.
This does not establish all services or survival across iOS/carrier updates,
SIM replacement or a full reset. Remove before iOS/carrier updates if you
need assured access to the current uninstall mechanism.

Repeated install recognizes healthy qualified ownership, including older
manual journals, and does not rewrite or rescan. This checks recorded
verification and the current reported profile, not a fresh catalog/signature
verification. Interrupted ownership or a changed reported profile stops
automatic installation rather than overwriting or blindly retrying.
--recover handles interrupted work; it leaves healthy installations applied.
Use 15, --remove-tmobile-wholesale or the printed --undo-gid1 command for
explicit uninstall. An unowned rule/stock Wholesale selection is untouched.
The original SIM may be disabled/removed. Item 4 is NOT Wholesale uninstall.

Keep the original runs folder and referenced backups when moving/upgrading
CarrierSIM; use --runs PATH if elsewhere. Removal discovers ownership there,
not from the carrier display name. Full ICCID appears in qualified journals
and catalog ZIPs even though console/log output masks it: keep them private.


ENABLING VoWiFi
A reboot after ordinary installation is recommended for full profile
application. Then check connectivity, calls and data. For automatic Wholesale,
follow the workflow above. Manual GID1 users should check their services and
use explicit undo when wanted.
1. Settings → Cellular → relevant SIM → enable Wi-Fi Calling (wording varies).
2. Enable Airplane Mode.
3. Leave Airplane Mode on, enable Wi-Fi and connect to a network.
4. Wait for the Wi-Fi Calling indication beside the carrier name, if your
   iOS version displays one.
5. Disable Airplane Mode. Automatic VoWiFi depends on the network/carrier.

macOS QUARANTINE AND EXECUTION PERMISSIONS
Downloads from the internet or Telegram receive com.apple.quarantine.
The launcher is unsigned, so macOS may block it with either message:
- “...is damaged and can't be opened. Move it to the Trash.” This can be
  download blocking, not corruption. Click Cancel, not Move to Trash.
- “...can't be opened because the developer cannot be verified.” Close it,
  then use System Settings → Privacy & Security → Open Anyway, if offered.

For either unsigned-file warning, open Terminal, type bash and a space,
drag Launch macOS.command into the window and press Enter. Running through
bash does not require the executable permission or Gatekeeper launch check.
To allow double-click launching, remove quarantine from the folder once
(change the path if needed; keep quotes):
/usr/bin/xattr -r -s -d com.apple.quarantine "$HOME/Downloads/CarrierSIM"
This removes only the folder/content quarantine attribute. -s avoids
following symlinks. sudo and disabling Gatekeeper are unnecessary.
For Permission denied:
chmod u+x "$HOME/Downloads/CarrierSIM/Launch macOS.command"
Inspect attributes:
/usr/bin/xattr -l "$HOME/Downloads/CarrierSIM/Launch macOS.command"
“No such xattr: com.apple.quarantine”: no attribute; quarantine is not the cause.
“No such file”: wrong path; locate the extracted folder in Finder.
“Operation not permitted”: move the folder to Downloads and retry with the
correct path; do not add sudo.
If macOS specifically detects malware, do not bypass that warning; download
again. Apple instructions: https://support.apple.com/102445

COMMAND LINE (replace python3 with py on Windows)
Install dependencies once from the script directory, preferably in a venv:
python3 -m pip install -r requirements.txt

python3 carrier.py --check          Check files/libraries, without a phone.
python3 carrier.py --status         Read SIMs and show a plan, without writing.
python3 carrier.py                  Use bundle.yaml for eligible SIMs.
python3 carrier.py --bundle O2_Germany --sims 1
                                    Another system bundle for SIM 1 only.
python3 carrier.py --restore        Remove IMSI symlinks (item 4).
python3 carrier.py --restore --sims 2
                                    Remove only SIM 2's IMSI symlink.
python3 carrier.py --recover        Recover after failure (item 5).
python3 carrier.py --wifi --status  Read-only plan over Wi-Fi (item 10).
python3 carrier.py --help           All flags.

For several phones, add --udid ID. USB is the default; --wifi selects the
network for normal actions, not the experimental GID1 modes.
For a nonstandard Windows Apple installation, provide library directories
from the same installed version:
py carrier.py --check --apple-dir "C:\path\Mobile Device Support" --apple-dir "C:\path\Apple Application Support"
Find Microsoft Store iTunes using PowerShell:
Get-AppxPackage AppleInc.iTunes | Select-Object -ExpandProperty InstallLocation
If WindowsApps loading produces WinError 5, copy root DLLs,
CoreFoundation.resources and AMDS64 DLLs into a separate folder. Use that
folder with --apple-dir when checking and installing. Keep files from one
version. The Apple Mobile Device service and USB driver are still required.
Apple calls/AirTraffic exchange live in airtraffic_apple.py; importing it
does not load DLLs. carrier.py logs and confirms the backup before the
final transfer by the separate AirTraffic process.
The script does not download target bundles or DLLs. Dependencies need
internet; optional bundle details are also fetched and cached online.
IMSI is not printed in the console. Phone logs (commcenter.log, device.log)
mask IMSI/ICCID as <imsi>/<iccid>, numbers as <num> and the device name as
iPhone. Older logs were not masked. ZIP archives in runs (catalog backups,
media-leftovers) contain IMSI and are not masked: do not publish them.
runs is excluded in .gitignore. If you use another --runs directory inside
a clone, exclude it yourself, for example /wifi-runs/ in .git/info/exclude.
The script does not change git rules.

WITHOUT A CABLE: Wi-Fi (item 10, --wifi)
Normal actions use the same services through usbmuxd over the local network,
like Finder Wi-Fi sync. This is slower than USB. Wi-Fi cannot establish
Trust. Experimental GID1 apply/undo remain USB-only.
One-time USB setup:
1. Connect USB, tap Trust, check item 2: SIMs should appear.
2. Finder → iPhone → General → Show this iPhone when on Wi-Fi → Apply.
   Windows: iTunes → Summary → Options.
3. Close Finder/iTunes and disconnect USB.
Then:
4. Put phone/computer on the same network (not guest, no client isolation).
   Keep the phone unlocked and awake.
5. Choose 10 (Wi-Fi), then 2. If SIMs appear, connectivity works. Then use 1.
6. If not visible, check step 2, sleep and Bonjour. If Trust is missing,
   repeat step 1.
7. After a write failure, rollback also uses the network. If the phone sleeps
   or disappears, the stage times out after 5 minutes. Failed rollback:
   reconnect USB, choose 10 → USB, then 5.
8. macOS requires Terminal's Local Network permission: System Settings →
   Privacy & Security → Local Network. Status can work without it, but
   direct AirTraffic transfer cannot. If prompted, click Allow; the script
   waits up to 20 seconds. No permission means a safe stop before writing.
   Recovery needs permission only for carrier-catalog restoration, not
   Books-only cleanup. tmux, screen and SSH cannot obtain permission;
   use Launch macOS.command (standalone: CarrierSIM) from Finder or an
   ordinary terminal window.
9. AirTraffic not responding over Wi-Fi within 170 seconds means network
   sync did not connect; check Local Network permission above. The script
   rolls back once, without another Wi-Fi attempt. Connect USB, select
   10 → USB and retry. If automatic recovery did not finish, use 5 over USB.

CHOOSING A BUNDLE: bundle.yaml
The file beside the script configures item 1:
  default: Vodafone_hu      default for Russian and Belarusian SIMs
  25001: Vodafone_ro        MTS Russia (MCCMNC without spaces)
An exact MCCMNC entry wins over default; without a file, use Vodafone_hu.
default applies only to Russia (MCC 250) and Belarus (MCC 257). Foreign SIMs
retain their profiles and are marked as untouched in item 2. Add their
MCCMNC or explicitly select their slot in item 7 (--sims 1 or 2) to change
them. Explicit slot selection applies regardless of carrier.
Use the folder name in /System/Library/Carrier Bundles/iPhone without
.bundle. Lines starting with # are comments. File errors show a line number
before connecting. Item 7 and --bundle override the file for that run.
If iOS does not select a requested non-Vodafone_hu bundle for a SIM, the
normal workflow restores the saved catalog.

ANOTHER BUNDLE (item 7, --bundle and --sims)
Enter a case-sensitive system folder name without .bundle, for example
O2_Germany, then choose SIM 1, 2 or both (Enter). “Both” does not change a
foreign SIM; select its slot explicitly. The confirmation keeps blank=yes.
The plan shows details from ios-bundles.github.io: home VoWiFi priority,
roaming VoWiFi, network label, iMessage activation number (Vodafone_tr uses
local Turkish 13191, unreachable from Russia), LTE/4G label, VoLTE switch,
5G, EVS, XCAP and visual voicemail. Vodafone_ro disables roaming VoWiFi.
These are iOS 27.0 bundle properties for iPhone 17, not live phone tests.
The table is cached in runs/bundles.json for a week; without internet,
details are omitted. A name differing only by case stops before writing;
an unknown name produces similar suggestions but is allowed to proceed.
AFC cannot read /System, so the phone's bundle list cannot be listed through
it. Existence is checked through selection after writing. If iOS selects
the requested bundle for no SIM (missing in that iOS or misspelled), the
script restores the pre-write catalog and rescans. An unselected SIM's
IMSI symlink is not changed.

EXPERIMENTAL GID1 OVERRIDE (items 12 and 13)
Manual expert workflow; acceptance and service depend on device/carrier.
For eligible T-Mobile MVNO-to-Wholesale use automatic item 14 above.
GID1 modifies only the user catalog, not Apple system bundles, signatures,
the matching algorithm or carrier provisioning. More specific aliases can
receive a higher native score; writing alone does not prove service.

The plain rule is <MCCMNC>_GID1-<HEX>, pointing through the existing bundle
link to the untouched system bundle. Optional --gid1-iccid appends
_ID-<ICCID> from the selected SIM's full ICCID. MCCMNC comes from that SIM.
GID1 is uppercase, even-length hex representing 1–16 bytes (2–32 digits).
Get the actual GID1 from a reliable source; do not guess it.
Selecting one slot identifies the trial SIM; the slot itself does not
isolate the rule. Plain GID1 rules can match other present/future SIMs with
the same MCCMNC/GID1. Qualified rules also require the recorded full ICCID.
Apply still conservatively refuses another enabled SIM on the same PLMN.
Item 4 is IMSI-only: it does NOT remove GID1 rules.

Observed plain-GID1 trial (2026-10-13):
iPhone 18 Pro, iPhone19,2, board V63AP, iOS 27.0.1 (24A446).
The tool wrote and read back 310240_GID1-69 -> TMobile_Wholesale_US.bundle.
CommCenter recognized the user alias, but listed the system candidate first
at an equal matching score. SIM2 stayed on TMobile_MVNO_US.bundle; SIM1
stayed on Verizon_LTE_US.bundle.
The failed target-selection check triggered automatic scoped undo:
the alias was removed, final catalog readback matched the backed-up
baseline, both active slots had verified baseline selections, and all
trial transfer journals reported Books restored with no recovery required.
Owner journal: runs/20261013-174048-gid-20a270/gid-alias.json
Terminal state: rolled_back.
The plain-GID1 trial failed to change selection; it did not demonstrate
a Wholesale signature failure. Wholesale was not selected in that trial,
so target-board signature acceptance and service/reboot behavior were
not tested.
The same-key alias is not a working override on this tested device/build.


Observed ICCID-qualified trial (2026-10-13), same device/build:
--gid1-iccid wrote and read back
310240_GID1-69_ID-<iccid> -> TMobile_Wholesale_US.bundle.
The captured log reported a higher native score for the qualified alias:
- Carrier/operator matching: stock MVNO 70.60; qualified Wholesale 80.60.
- Carrier lookup: stock MVNO 76.00; qualified Wholesale 86.00.
Both SIM2 carrier and operator links resolved to the untouched system
TMobile_Wholesale_US.bundle. Verification was not skipped and returned
Success. This proves observed selection/signature acceptance on this build,
not calls/data, VoWiFi/VoLTE or persistence after reboot.
A later lookup included a private GID2 field, but no usable value for
constructing another rule was exposed.
The connection dropped during log capture. Trigger installation completed,
but the incomplete capture was refused; apply did not reach applied_verified.
The first automatic rollback also lost its connection. After reconnecting
by USB, journal-bound undo repaired the interrupted transfer and finished
with a fresh, complete rescan. Its first verification failed, but scoped
recovery succeeded; the command still exited with the original capture error.
Owner journal: runs/20261013-183644-gid-d8ed8e/gid-alias.json
Terminal state: undone. The qualified alias was removed and final catalog
readback equaled the pre-trial backup. Carrier AND operator selections
returned to Verizon_LTE_US.bundle for SIM1 and TMobile_MVNO_US.bundle for
SIM2. All nine transfer journals reported Books restored, with no active
trial recovery remaining. The stock bundle and matcher were not modified.
Preserve the private artifacts in this run and its recovery directories.


Retained service trial (2026-10-13), explicitly requested:
The qualified Wholesale rule was left installed on SIM2. Unified logging
avoided legacy replay but did not prevent the first carrier switch from
closing the device session. Automatic failure handling removed the catalog
rule before Books cleanup lost the connection; Wholesale stayed cached as
active. Fresh-session recovery repaired the consumed transfer's Books
cleanup. A catalog snapshot proved the rule was absent before restoring
its exact owned entry, without overwriting unrelated catalog entries.
The retained install passed catalog readback, stable SIM identity checks
and a fresh, complete rescan. SIM2 carrier AND operator selected
TMobile_Wholesale_US.bundle with successful signature verification.
SIM1 remained verified on Verizon_LTE_US.bundle for both selections.
The phone reported Wholesale version 72.0. Books cleanup was verified and
no transfer recovery remained pending. The user subsequently reported that
it works and persists through reboot; no unreported service checks are inferred.
Owner: runs/20261013-190456-gid-1fa4b6/gid-alias.json
State: applied_verified.
Evidence: runs/20261013-191437-gid-retain-37f2b3/retained-rescan
No undo was run after the retained installation. The implementation now
reconnects and verifies in place after qualifying connection loss before
deciding whether scoped rollback is required. --recover leaves healthy
installed journals alone. On the tested Mac, undo only when explicitly wanted:
.venv/bin/python3.12 carrier.py --undo-gid1 "runs/20261013-190456-gid-1fa4b6"


Use USB, keep the phone unlocked and preserve the apply run directory.
CLI examples (HEX and NAME are placeholders for actual values):
python3 carrier.py --gid1 HEX --bundle NAME --sims 1 --status
  Read-only plan. Explicit bundle and exactly one slot, 1 or 2, are required.
python3 carrier.py --gid1 HEX --bundle NAME --sims 1
  Apply once. This is a write action.
python3 carrier.py --undo-gid1 "runs/RUN_DIRECTORY" --status
  Read-only preview using that operation's saved gid-alias.json.
python3 carrier.py --undo-gid1 "runs/RUN_DIRECTORY"
  Remove only the owned alias, rescan and verify undo.
Use --sims 2 for slot 2. On Windows use py. Standalone builds use CarrierSIM
or CarrierSIM.exe instead of python3 carrier.py.
Menu 12 collects GID1, an explicit system bundle and one SIM, then previews.
Menu 13 collects the literal apply run directory, then previews undo.
Both need y/yes to write; default NO, Enter cancels. Empty inputs cancel.
With Wi-Fi selected, these menu actions refuse before collecting inputs:
switch menu 10 to USB first. No implicit network setting is inherited.

--gid1 and --undo-gid1 are mutually exclusive and incompatible with other
write, diagnostic or check actions; --status is allowed. Do not use --wifi.
There is no automatic multi-apply retry for this experiment.

ICCID-qualified trial (CLI):
python3 carrier.py --gid1 HEX --gid1-iccid --bundle NAME --sims 2 --status
python3 carrier.py --gid1 HEX --gid1-iccid --bundle NAME --sims 2
Use the full ICCID reported for the selected SIM, not guessed bytes.
This narrows the rule but does NOT promise a higher score or acceptance.
The fresh baseline must still choose the plain MCCMNC_GID1-HEX key.
Carrier AND operator selections must verify for every enabled slot:
both target selections must resolve to the requested system bundle;
other slots must remain at their corresponding baseline selections.
Undo verifies both layers for unchanged original SIM identities.
--gid1-iccid requires --gid1. Undo reads the qualifier from the journal
and checks it against the original selected-SIM ICCID hash, even when that
SIM is removed. Do not pass --gid1-iccid to undo.
Menu 12 remains plain GID1; menu 13 can undo either saved rule form.
CLI/syslog output masks the ICCID. The exact alias name in the recovery
journal and catalog archives contains it: keep these artifacts private.

Read-only previews do not install triggers, export the catalog or back up
Books. Alias absence, owned-entry checks and acceptance are not established
by preview; confirmed operations perform the catalog/recovery checks.
Before committing the alias, require:
- a fresh carrier-catalog and Books backup, with hashes;
- correct device/family binding and stable hashed IMSI/ICCID bindings,
  including all other enabled SIMs;
- an absent alias and recorded exact symlink bytes;
- baseline slot selections and durable lifecycle state in gid-alias.json;
- a fresh baseline rescan proving that the selected SIM chose the exact
  <MCCMNC>_GID1-<HEX> matching key, with verified baseline choices, followed
  by an identity recheck before writing. A resolved bundle name or score
  alone does not prove that key.
Existing aliases, unsupported families, known-closed AirLift builds,
missing/unstable identifiers and missing evidence stop safely. There is
no guessed fallback. An independent, non-overlapping, device-board-signed
rescan trigger is required. Trigger installation alone does not prove
acceptance of the target bundle.
Trigger rescans use the live unified CommCenter logging service also used by
diagnostics. Historical entries are disabled and collection starts after
fresh CommCenter activity. Identifiers are masked before disk. This avoids
the legacy relay's buffered-line replay without treating old selections as
fresh proof. A dropped stream invalidates that capture. After completed write/
readback, actual connection loss permits same-phone reconnect, catalog and
identity checks, and one fresh verification-only rescan; partial logs alone
are never success.

Selection proof needs the target Resolved path, correct slot link and
successful verification. Other active slots must stay at baseline choices.
Rejected, unverified or missing-log selection automatically triggers scoped
undo: remove the exact owned alias from the current catalog, preserving
unrelated changes, then rescan and verify rollback. Missing catalog, Books
or recovery verification is NOT completed rollback.
A successful apply remains installed for normal use and service/reboot checks.
Save the run directory and exact undo instructions printed by the tool.
Check calls, data, VoWiFi/VoLTE and the other services you rely on; selection
does not prove them. Undo is explicit, not an immediate post-install step.
Remove the rule before iOS/carrier updates if you need assured access to
today's removal mechanism: an update can replace it or close the write path.

Undo is bound to the device and exact owned entry, not continued presence
of the original SIM. It works after that SIM is disabled/removed. A changed
entry is refused. An absent entry is idempotent only with verification.
Exceptions, disconnects, interrupts and process-crash journals preserve
recovery state. Reconnect by USB and use saved undo/recovery instructions;
never blindly reapply. Item 5/--recover performs bound undo for INTERRUPTED
GID journals, not another apply; healthy applied_verified profiles stay applied.
Existing transfer/Books recovery remains usable. Deliberate removal uses 13,
15 for owned qualified Wholesale, or the printed --undo-gid1 command.
With no active SIMs, undo verifies catalog/Books restoration but reports
slot selection as untested. Enable a SIM before checking selection/service.
Undo reports the SIMs present at final verification, not an earlier preview.
A crash before device-change intent can be cancelled without a trigger.
After final authorization, an unconsumed final source is ambiguous: stop
instead of blindly replacing the catalog. Stage repair refuses to replace
a different current catalog. An identical alias created by another operation
before our final transfer was authorized is not owned and is preserved.
--restore-backup "runs/RUN_DIRECTORY" is a fallback restoring the WHOLE
saved catalog. It can overwrite unrelated changes since backup; prefer
owned-entry undo. Preserve backups/journals until undo and recovery verify.
Hashed identity records do not make catalog archives safe to publish.

INSPECTING A RETAINED ESIM TRIAL
1. Settings -> General -> About: scroll to the relevant eSIM section and
   inspect Carrier and its version. The name/version need not visibly
   change; About alone does not identify the selected bundle.
2. Settings -> Cellular / Mobile Service -> the trial eSIM: inspect
   Voice & Data and Wi-Fi Calling. Note which options are available.
   Dismiss carrier-settings update prompts while the experiment is installed.
Explicitly select that eSIM for cellular data and outgoing calls. Turn off
Wi-Fi for the cellular-data check; prevent another SIM's automatic data
switching from hiding a failure. For Wi-Fi Calling, use Airplane Mode with
Wi-Fi enabled and make an actual call; a carrier label does not prove service.
Preserve the apply run and exact undo command. A successful apply stays
installed until you explicitly undo it.


BACKUPS AND RECOVERY
Operations are saved in runs/DATE-ID with catalog backups, logs and errors.
Do not delete runs during an operation. Do not run several copies at once.
Ordinary completed runs need not migrate to a new script version. Before
installation, the script cleans its own old airlift-* folders in
/var/mobile/Media and its entries in Books lists/download queues. Exported
catalog leftovers are saved in the current run's media-leftovers before
removal. Preserve active GID/Wholesale journals and referenced backups when
upgrading the software, using --runs PATH if they are elsewhere. Remove before
iOS/carrier updates if you need today's write path; do not discard recovery state.
Item 4 (--restore) removes root-level 15-digit IMSI symlinks. MCCMNC links,
GID aliases, packages and other files remain. --sims 1/2 removes only that
SIM's IMSI link; if absent, the catalog is unchanged. It then reads back,
rescans and reports iOS selections. This disables IMSI binding, not phone
settings. For GID1 use 13; for owned qualified Wholesale, 15.
Item 5 (--recover) finds unfinished operations for this device and restores
the pre-operation catalog. If writing completed and only Books cleanup
failed, it cleans Books without rolling back the installed profile. If the
catalog had not yet been moved (no exported copy on the phone), only
AirTraffic's Books support files need cleanup, without another sync.
Several unfinished operations are handled newest first. Unfinished normal
operations block items 1/4/7 until recovery. Experimental journals likewise
must not be bypassed by another apply; use saved undo/recovery instructions.
Healthy GID/Wholesale profiles are installed, not interrupted: item 5 leaves
them applied. Use explicit removal rather than recovery to uninstall.
Normal operations recover after disconnects before retrying. Failed
recovery cancels another write: use item 5. This is not a multi-apply retry
policy for the experiment.
Restore a particular complete catalog manually:
--restore-backup "runs/DATE-ID"
The backup follows trigger installation, so the trigger remains in it.
Explicit stage recovery: --recover "runs/DATE-ID/STAGE".

COMMON PROBLEMS
Log disconnected in items 8/9: Airplane Mode may close the stream. The
script reconnects until the time limit and reports the count. After more
than 20 seconds without logs (Airplane Mode over Wi-Fi may break the whole
connection), report what was collected. No log at all means failure.
Books/Books.plist cannot be changed while script entries remain: a known
cause is iCloud Backup during a stage (#44). Preserve runs/backups. This
sequence helped in one case:
1. Disconnect USB.
2. Close all phone apps in the app switcher.
3. Force restart: quickly press/release Volume Up, then Volume Down, then
   hold the side button until the Apple logo.
4. Unlock after startup and reconnect.
5. Retry item 5 (--recover) in the same CarrierSIM folder. If it fails,
   send the error text/session log to the author.
Apple: https://support.apple.com/guide/iphone/force-restart-iphone-iph8903c3ee6/ios

Windows sees Apple iPhone/photos in Explorer, but iTunes/the script cannot:
Apple Mobile Device USB Driver may be missing. Download it from Microsoft
Update Catalog (https://www.catalog.update.microsoft.com), extract the .cab
and run pnputil /add-driver usbaapl64.inf /install in that folder from an
administrator command prompt. Restart. Reinstalling iTunes may not help.

The iPhone rejected Apple components (formerly an AirTraffic premature-sync
failure), with “Grappa session could not be established” in phone logs:
the phone rejected AirTraffic. On Windows ATHostConnectionSendSyncRequest
could cause this even during backup. The script now creates the connection
with ATHostConnectionCreateWithLibrary and sends RequestingSync with
prepared Grappa data. AirTraffic success alone proves no VoWiFi/EVS/5G.
If it persists, retrying unchanged will not help. Uninstall iTunes,
including any Microsoft Store version, install full iTunes x64:
https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362
A 404 may require registration, login and some forum activity. Use the full
installer, not just AppleMobileDeviceSupport64.msi; launch once and retry.
One case needed 12.11.0.26 and removal of the old iTunes library first.

InstallProhibited: Screen Time (Content & Privacy Restrictions → iTunes &
App Store Purchases → Installing Apps: Allow) or MDM prohibits installation.
No phone changes are made in that case.
Books support files keep changing: close Books and wait for downloads.
The script reads sync files only (Books.plist, Sync/*, download queue), not
book contents; library size does not affect it.
If the directory is not writable, move CarrierSIM to Downloads.
CarrierSIM's version appears in the menu, session.log, environment.json and
diagnostics. -dev denotes development; the script-build identifier is the
first 12 SHA256 characters of carrier.py. No phone is needed for --version.

VERIFYING THE RESULT
The normal workflow reads back the written catalog, reinstalls the trigger
and inspects CommCenter's chosen bundle/signature for each SIM. Exit codes:
0 completed, 1 error, 3 selection not confirmed after writing.
It reports accepted/rejected verification, a different selected bundle or
no selection. For no selection, inspect the current-bundle line in item 2.
If another bundle was selected, normal installation restores settings. If
the rescan log is empty, normal IMSI installation can leave the catalog
written; toggle Airplane Mode for 15 seconds and check item 2. This differs
from GID1: missing/unverified evidence causes scoped undo.
A rescan failure can occur after the catalog was written; inspect
runs/DATE-ID/error.json. Successful bundle selection does not prove 5G or
individual bands. Reboot for full profile application, then test calls,
data and connectivity. CountryBundles is not changed, but iOS may choose
a different country/serving-network bundle following the carrier bundle.

CONNECTION DIAGNOSTICS (items 8/9, --diagnose/--watch-call)
Read-only. Item 8 reads CommCenter logs for up to 90 seconds: enable
Airplane Mode, disable after 10 seconds and leave Wi-Fi on. After all SIMs
re-register with IMS, wait 10 seconds and finish; Enter finishes earlier.
Report IMS, VoWiFi, network and 5G SA per SIM, plus the VoWiFi tunnel ePDG,
latest IKE state/error. ePDG 127.0.0.1 or 0.0.0.0 is a placeholder and
cannot reach the carrier with any bundle. A DNS block by router, ad filter,
VPN or carrier is common. Full IKE details, with numbers masked:
runs/DATE-diagnose/commcenter.log
Item 9 listens up to 180 seconds during a test call, showing transport and
codec; press Enter after the call.

PROFILE REPORT (item 11, --report)
Read-only. Read CommCenter for 90 seconds (Airplane Mode on, off after
10 seconds, Wi-Fi on), then answer per-SIM checks: VoWiFi with/without
Airplane Mode, VoLTE without 3G fallback, band from *3001#12345#*, iMessage
by number, SMS over Wi-Fi, tethering and merged calls. Answer y/yes, n/no
or Enter (not tested). Without a terminal, questions are skipped and the
report notes that manual checks were not supplied. The block between
===== Profile report and ===== End of report is saved as
runs/DATE-report/report.txt: model, iOS, carrier, profile/version, log
summary and answers. Phone numbers, IMSI and ICCID are omitted. Paste it
into the forum or the Profile report issue template. Bundle tables alone
do not establish which profile works best with a carrier.

WAITING AND RETRIES
Normal operations wait up to 180 seconds for connection/unlock. After a
phone is chosen, reconnection is restricted to it. Temporary connection
errors allow up to 3 attempts; permanent model/signature/backup errors do
not retry. Configure --attempts 5 --wait-seconds 300. Automatic rollback
also reconnects/retries up to --attempts. Experimental GID1 does NOT
perform automatic multi-apply retries; its saved lifecycle guides recovery.
After the backup/log-path message, installation is still running: wait for
stages [1/4]–[4/4] and completion with SIM 1 / SIM 2 results.

HOW IT WORKS
CommCenter chooses the bundle. The Apple-signed system bundles are on the
read-only /System/Library/Carrier Bundles/iPhone partition. User catalog:
/var/mobile/Library/Carrier Bundles/iPhone (IPCC updates and symlinks).
Only the user catalog is changed. Family-specific iPad paths are used.
USB or --wifi accesses services available after Trust:
- AFC (com.apple.afc): file I/O within /var/mobile/Media only, not /System
  or /var/mobile/Library;
- installation_proxy: IPCC installation;
- syslog_relay / unified OS trace: diagnostics and fresh CommCenter evidence;
  GID rescans disable historical entries in the unified stream;
- streaming_zip_conduit: ZIP upload/extraction into /var/mobile/Media;
- com.apple.atc (AirTraffic): iTunes/Finder sync. macOS/Windows use Apple's
  AirTrafficHost; Linux uses pymobiledevice3. The native Linux protocol is
  based on MIT AirCard-Linux projects:
  https://github.com/shinkuan/AirCard-Linux
  https://github.com/hoicau/AirCard-Linux
The first four services use pymobiledevice3.
Normal installation:
1. Install a signed non-overlapping carrier IPCC trigger. This forces
   reselection and creates the catalog on a clean device. Trigger remains.
2. Move the catalog to /var/mobile/Media, read over AFC and save
   runs/DATE-ID/snapshot/original.zip; put it back.
3. Add root symlinks named with the SIM's 15-digit IMSI, targeting
   ../../../../../../System/Library/Carrier Bundles/iPhone/Vodafone_hu.bundle
   (or --bundle). Leave the rest unchanged.
4. Read back/compare, reinstall trigger, inspect selection/signature logs.
AFC cannot write outside Media. AirLift uploads a ZIP tree into Media with
streaming_zip_conduit; AirTraffic moves it using a relative path during
synthetic Books sync. AirTraffic support files in /var/mobile/Media/Books
are removed; Books is compared with its backup.

EVS
With Apple's normal bundles, EVS does not work in Russia starting with
iPhone 14, or in Belarus. Vodafone_hu enables it; actual use in a particular
call is determined by the network.

5G / NR BANDS
Vodafone_hu NR band configuration:
Belarus (MCC 257): n1, n3, n7, n8, n20, n28, n38, n40. n78 unavailable;
  n79 operation is not claimed.
Russia (MCC 250): n1, n3, n7, n8, n20, n38, n40. n78/n79 unavailable;
  n28 is not listed.
Each band's real-world operation has not been tested. Connection depends
on coverage, network configuration, model, iOS and LTE+NR combinations
(NSA). A 5G setting alone does not prove 5G registration.
Changing bundles does not remove regional NR restrictions. Signed GRI in
system Default.bundle sets allowed bands; logs show that GRI still loads
from Default.bundle after a bundle change. The script does not alter GRI.
Other bypass methods have not been researched.
“4G” instead of “LTE” is only a label and does not affect connectivity.

MODELS AND iOS
Supports iPhone 12–18, including mini, Pro, Pro Max, Plus, Air and e, with
iOS 16 or later.
  iOS   Works                         Does not work
  27.2  27.2 beta 2 (24B5089g)         starting with beta 3 (24B5099f)
  27.0  27.0.1 (24A446)               —
  26    26.7.1 (23H30)                —
  18    18.7.10 (22H374)              —
  17    17.7.2 (21H221)               —
  16    16.7.16 (20H392)              —
  15    not tested: 15.8.8 (19H422)    —
  14    not tested: 14.8.1 (18H107)    —
Latest branch builds are listed by AppleDB (appledb.dev). Apple closed the
write mechanism in iOS 27.2 beta 3 and later. Normal IMSI operations warn
and stop if transfer is refused; GID1 apply refuses known-closed builds.
Normal model/board support information is not a guarantee of a working
transfer; GID1 requires supported family and verified baseline evidence.
Future iOS compatibility is not guaranteed.
Bundled AVEA_tr, Swisscom_ch and O2_Germany 72.1 triggers come unchanged
from Apple's CDN and the official iOS 27.0 catalog; checksums were verified.
All three contain signed settings for boards in the list below. Normal
installation warns, rather than blocking, when board settings are absent.
The GID1 experiment's independent trigger/baseline requirements are stricter
and do not claim acceptance of a target bundle on an untested board.

Incomplete known-model reference (not an allowlist; unknown models are
shown by ProductType):
- iPhone 12: iPhone13,2 / D53GAP
- iPhone 12 mini: iPhone13,1 / D52GAP
- iPhone 12 Pro: iPhone13,3 / D53PAP
- iPhone 12 Pro Max: iPhone13,4 / D54PAP
- iPhone 13: iPhone14,5 / D17AP
- iPhone 13 mini: iPhone14,4 / D16AP
- iPhone 13 Pro: iPhone14,2 / D63AP
- iPhone 13 Pro Max: iPhone14,3 / D64PAP
- iPhone 14: iPhone14,7 / D27AP
- iPhone 14 Plus: iPhone14,8 / D28AP
- iPhone 14 Pro: iPhone15,2 / D73AP
- iPhone 14 Pro Max: iPhone15,3 / D74AP
- iPhone 15: iPhone15,4 / D37AP
- iPhone 15 Plus: iPhone15,5 / D38AP
- iPhone 15 Pro: iPhone16,1 / D83AP
- iPhone 15 Pro Max: iPhone16,2 / D84AP
- iPhone 16: iPhone17,3 / D47AP
- iPhone 16 Plus: iPhone17,4 / D48AP
- iPhone 16 Pro: iPhone17,1 / D93AP
- iPhone 16 Pro Max: iPhone17,2 / D94AP
- iPhone 16e: iPhone17,5 / V59AP
- iPhone 17: iPhone18,3 / V57AP
- iPhone 17 Pro: iPhone18,1 / V53AP
- iPhone 17 Pro Max: iPhone18,2 / V54AP
- iPhone 17e: iPhone18,5 / V159AP
- iPhone Air: iPhone18,4 / D23AP
- iPhone 18 Pro: iPhone19,2 / V63AP
- iPhone 18 Pro Max: iPhone19,7 / V64SAP
- iPhone 18 Pro Max (U.S.): iPhone19,3 / V64AP

FAQ AFTER INSTALLATION
Ordinary IMSI profile after update/reboot: forum users report persistence
from 27.0 to 27.0.1 and through reboots. Since 27.2 beta 3 (issue #56),
AirTraffic cannot move into the catalog. Existing profiles may remain, but
cannot be installed/changed/removed with this method. beta 1/2 still allow
writing. Normal operations warn and attempt on newer versions, stopping
before changes if refused. Do not update if you still need profile changes.
An ordinary IMSI binding can stop matching a replacement SIM or be removed
by a full phone reset; Reset Network Settings does not remove it. The qualified
Wholesale user separately reported reboot persistence. No future iOS/carrier
update guarantee follows: remove before updating if you need today's write path.

Changed SIM and lost profile: new SIM/reissued eSIM/eSIM-to-physical transfer
changes IMSI. Install again with item 1 or 7.
iMessage/FaceTime activation after SIM change: this also happens with the
normal bundle and item 4 may not help. Several users fixed it with another
SIM/eSIM (new ICCID), activating in 10–60 minutes. Install again afterward:
IMSI also changes. SMS may be restricted for the first 24 hours after SIM
replacement, temporarily preventing phone-number activation. Wait/retry.

5G icon but LTE speed: dial *3001#12345# for Field Test. n-bands (n1, n7,
n40) are NR, b-bands LTE. NSA is 5G over LTE; only b-bands means LTE.
See the regional n78/n79 limitations above.
Missing VoLTE switch: Vodafone_hu, Vodafone_ro and Vodafone_tr, among
others, leave VoLTE always enabled. Turn Wi-Fi off and call; no 3G fallback
and working data indicate VoLTE.
EVS unavailable: both parties must support it and be on IMS (VoLTE, VoNR or
VoWiFi). Short codes/support almost always use AMR; call an EVS subscriber
on the same carrier, not 611 or 0890. EVS outgoing but AMR-WB incoming:
reboot. The author observed full reboot was needed; Airplane Mode and
reinstallation did not help. Item 9 shows codecs.
Slower data/MMS failures: donor APN settings may have replaced yours.
Settings → Cellular → [line] → Cellular Data Network: enter your carrier's
APN/MMS from its website if wrong, then toggle Airplane Mode. If menu absent
or APN correct, coverage is likely; forum speeds varied by Moscow cell site.
VoWiFi only in Airplane Mode/weak signal: bundle/carrier policy decides;
some prefer cellular at home. Compare at ios-bundles.github.io.
“Vodafone TR Wi-Fi” or “4G” label: set by the signed bundle, not connectivity.
Changing it requires a different bundle, not editing Apple's signed files.
Carrier settings update offer: one v3 user accepted it and lost 5G; a
Vodafone_tr user receives it daily, another never. Evidence is incomplete,
so declining is safer. If ordinary IMSI profile lost, reinstall via 1/7 and
report in GitHub issue #16. GID1 must be undone before any update.

MegaFon SMS over VoWiFi: calls work, SMS not for everyone. Before October 2,
2026, no forum user reported successful SMS. On October 2, two Vodafone_hu
users sent in Airplane Mode; another could not. Test a normal number:
short-code SMS does not work over VoWiFi even with the normal bundle.
Beeline SMS over VoWiFi is reported working.
Beeline switch present but no VoWiFi: check service enabled on the number.
Forum users report Beeline disabling it when the SIM was in an unsupported
phone. Ask support to enable it (they may refuse), or enable it with the
SIM in Android/iPhone 13 or earlier, then return it to the iPhone.
T2 VoWiFi: before October 1, 2026, forum iPhone reports only had limited
success with Nova_is plus a DNS ePDG override. Overnight October 2 it began
working automatically without DNS/reinstall in Moscow, Saint Petersburg,
Nizhny Novgorod, Volgograd, Omsk, Bryansk and Kaluga Region, on Vodafone_hu,
MTS_ua and Telia_az. Perm and parts of Moscow still reported failure;
bundle changes did not help. T2 appears to enable it regionally: wait and
occasionally toggle Airplane Mode. T2's website is also quoted as excluding
“City number”, “Second number”, “Corporate PBX”, CVPN and CSD; bundles cannot
fix those service conflicts.

VERIFIED (EXISTING IMSI WORKFLOW, NOT GID1)
Linux USB full installation: iPhone 14, 14 Pro, 15 Pro, 15 Pro Max, 17,
17 Pro with iOS 18.7.7 and 26.x. iPhone 14 Pro: restore both SIMs and then
install only for SIM 1. Probe without file transfer:
bash "Launch Linux.sh" --atc-probe
USB iPhone 15 Pro / iOS 27.0.1 (24A446), MTS: recovery (5) then normal-profile
restore (4), status confirmed MTS_ru, Books support files restored.
Linux Wi-Fi has not been separately tested.

| iPhone | iOS (build) | SIM → bundle |
| --- | --- | --- |
| 14 | 26.3 (23D127) | MTS → Vodafone_ro |
| 14 Pro | 26.3.1 (23D8133) | Yota and T2 → Vodafone_hu |
| 15 Pro | 18.7.7 (22H340) | Beeline → Vodafone_hu |
| 15 Pro Max | 26.6.1 (23G83) | Beeline → Vodafone_hu |
| 17 | 26.6.1 (23G83) | MTS → Vodafone_ro |
| 17 Pro | 26.6.2 (23G90) | MTS eSIM → Vodafone_ro |
VoWiFi call also confirmed on iPhone 17. These checks were on Linux.
Full cycle iPhone 17 / iOS 27.0 (24A437): both SIMs selected Vodafone_hu,
signatures accepted. Wi-Fi installation: iPhone 16 Pro Max / iOS 27.0.1
(24A446), macOS. AirTrafficHost/CoreFoundation loading: macOS Apple Silicon.
Without a phone: SIM selection, model table, system links, file preservation,
recovery, stopping before writing; dependency installation Python 3.11–3.14.
Physical Windows use has not been verified. This is experimental, without
a guarantee for every computer or future iOS. Qualified Wholesale has live
selection/target-board evidence and user-reported working/reboot persistence
on iPhone 18 Pro / iOS 27.0.1. Fresh automatic installation has not been
repeated on physical hardware.

SOURCES
Target bundles: Apple's signed files already in the device's iOS.
iOS 27.0 / 72.1 IPCC triggers:
AVEA_tr:
https://updates.cdn-apple.com/20260914/carrierbundles/140-93457/2A35FABC-3831-479D-9128-B57AD2C94A2D/AVEA_tr_iPhone.ipcc
SHA256 bb6bf489f93da2d3ef9f8a69ce819d6b90588af8177116b7ff2c8233811c7cac
Swisscom_ch:
https://updates.cdn-apple.com/20260914/carrierbundles/142-02578/9860B1D5-0761-4E45-A9C6-2161EDA569AD/Swisscom_ch_iPhone.ipcc
SHA256 2fe320c72e85853942ba1073d80f219cdee211a0423f5d980978a492662a999f
O2_Germany:
https://updates.cdn-apple.com/20260914/carrierbundles/142-17747/8DA66AA2-999A-4D68-B89B-18312F54884D/O2_Germany_iPhone.ipcc
SHA256 5f8166b3e33d14230273b85320795f1eb1f29214d2c0594056d0b0e448c029f1
The trigger must not overlap a SIM or chosen bundle. The script uses the
first suitable of three; O2_Germany covers when AVEA_tr/Swisscom_ch cannot.
Catalog:
https://itunes.apple.com/WebObjects/MZStore.woa/wa/com.apple.jingle.appserver.client.MZITunesClientCheck/version/
iPad: AVEA_tr_iPad.ipcc 72.1 from the same Apple catalog (iPad / 27.0):
https://updates.cdn-apple.com/20260914/carrierbundles/140-93455/B633E5DE-90AC-41D9-A263-01285F47EEE3/AVEA_tr_iPad.ipcc
SHA256 cb0f39ab4500d4348132d40f54cb56e78a42289fc44125bbd45ddc1460e20249
Device table: https://api.ipsw.me/v4/devices?type=ipsw (2026-09-27).
iPhone 18 Pro: https://www.apple.com/iphone-18-pro/specs/
Transfer protocol: AirLift (0xjohnnydev/airlift, c75b3ea), MIT license
included in LICENSE-AirLift.txt. https://github.com/0xjohnnydev/airlift
Windows Grappa adapted from dhava-gautama/AirCard-Windows, src/airtraffic.rs,
commit e0eadb0c88da55516f1c9f939fd4ce27560910d5; MIT in LICENSE-AirCard.txt.
Prepared Grappa data in that code came from yinyajiang/go-tunes.

LOCAL CHANGES
- USB-only automatic Wholesale install/remove: discover the actual matched
  GID1 key, qualify by full ICCID, verify carrier/operator selection, and
  leave success applied. Older qualified ownership is recognized unchanged.
- English user-facing code/documentation and Launch macOS.command,
  Launch Windows.cmd, Launch Linux.sh. Existing IMSI behavior and saved
  historical runs remain unchanged.
- Connection-loss verification reconnects without reapplying the alias;
  generic recovery leaves healthy profiles applied. Regressions cover auto
  selection, real-key discovery, idempotence, scoped undo, changed identities/
  ownership and interrupted verification. 172 tests run, 2 skipped; suite
  passed. Six isolated actual CLI/menu scenarios passed with device transport
  simulated; the user's retained manual qualified install has live evidence
  and reported working/reboot persistence.
- Fixed the post-apply readback connection-loss gap, including interrupted
  readback stages and the analogous removal path. AFC future ownership is
  cleaned up without suppressing the original transport failure.
  177 tests run, 2 skipped; suite passed. Before/after reproductions cover
  readback disconnects and the pinned SDK's send/receive race. Actual CLI
  smoke passed install, repeat/no-op and removal with injected disconnects.
- Physical verification: repaired the failed owner and interrupted Books
  stage, then completed a fresh automatic install despite a live rescan
  connection drop. SIM 1: T-Mobile Wholesale 72.0; SIM 2: AT&T MVNO 72.0
  unchanged. Both carrier/operator signatures verified. Read-only follow-up
  confirmed unchanged phone/SIM identities, all four native stages restored,
  live Books sync files matching backup and zero unfinished operations.
  Repeat installation was a no-op; Wholesale remains applied. Calls, data
  and reboot persistence were not re-tested in this session.

LICENSE
CarrierSIM's own code is MIT (LICENSE).
Copyright (c) 2026 Vladimir B / vlw and contributors.
Preserve copyright notice/license with copies or substantial code portions.
Third-party code retains its licenses, including LICENSE-AirLift.txt and
LICENSE-AirCard.txt. CarrierSIM's license does not cover Apple files:
assets.zip IPCC triggers, system carrier bundles or Apple libraries; rights
belong to their respective holders.
Research, development and testing: Vladimir B / vlw (vlwwwwww@gmail.com).
