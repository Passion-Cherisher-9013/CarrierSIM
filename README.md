# CarrierSIM

[Discussion on 4PDA](https://4pda.to/forum/index.php?showtopic=1055886&view=findpost&p=145261196)

CarrierSIM changes SIM-to-bundle rules in the iPhone's user carrier catalog without modifying Apple-signed system bundles. The default IMSI workflow selects **Vodafone_hu** or another existing bundle to enable **Wi-Fi Calling (VoWiFi)** in Russia; it can also enable EVS and 5G on some bands. The separate **automatic T-Mobile Wholesale workflow** supports eligible SIMs, including eSIMs, currently using `TMobile_MVNO_US`.

The Python script runs on **macOS**, **Windows** and **Linux**. Standalone builds that do not require Python are available for macOS, Windows x64 and Linux x64.

Supports **iPhone 12–18**, including **mini, Pro, Pro Max, Plus, Air and e**, with **iOS 16 or later**.

| iOS | Works | Does not work |
| --- | --- | --- |
| 27.2 | ✅ 27.2 beta 2 (24B5089g) | ❌ starting with 27.2 beta 3 (24B5099f) |
| 27.0 | ✅ 27.0.1 (24A446) | — |
| 26 | ✅ 26.7.1 (23H30) | — |
| 18 | ✅ 18.7.10 (22H374) | — |
| 17 | ✅ 17.7.2 (21H221) | — |
| 16 | ✅ 16.7.16 (20H392) | — |
| 15 | ❔ not tested, 15.8.8 (19H422) | — |
| 14 | ❔ not tested, 14.8.1 (18H107) | — |

These are the latest builds of each branch listed by [AppleDB](https://appledb.dev/firmware.html?filter=release;iPhone,+iPod+touch). Apple closed the write mechanism in iOS 27.2 beta 3 and later: the normal IMSI workflow warns and stops if the iPhone refuses the transfer. GID1 and automatic Wholesale installation refuse known-closed builds before applying an alias.

**iPad Wi-Fi + Cellular:** an active SIM or eSIM is required; installation has not yet been tested on a device. Wi-Fi-only models without a cellular modem cannot install profiles.

> Compatibility with future iOS/carrier updates is not guaranteed. The ICCID-qualified Wholesale installation selected and verified both carrier and operator bundles on iPhone 18 Pro / iOS 27.0.1; the user subsequently reported that it works and persists through reboot. This does not establish every service, carrier, device or update combination.

## Requirements

- **Python 3.11 or later**, preferably 3.12. Windows requires 64-bit Python.
- **Windows:** install iTunes x64 using the [4PDA instructions](https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362). The Microsoft Store version and Apple Devices may not work. A 404 when downloading from 4PDA can mean that registration, login and some forum activity are required.
- **macOS:** no additional software is required.
- **Linux:** install the system `usbmuxd` service (for example, `sudo apt install usbmuxd` on Debian/Ubuntu). `--check` reports whether it and its service socket are available.
- An iPhone, a USB cable and internet access for the first launch. Later, normal IMSI operations can use Wi-Fi (menu item **10**). GID1 and automatic T-Mobile Wholesale installation/removal are **USB-only**.

If the device is not detected, unlock it, allow connections in Settings → Privacy & Security → Wired Accessories, and reconnect the cable. On Linux, if necessary: `sudo systemctl restart usbmuxd`.

## Getting started

1. Extract the entire archive into Downloads.
2. Connect the iPhone by USB, unlock it and tap Trust. Close Finder and iTunes if they are synchronizing.
3. Run the launcher for your OS:
   - macOS: `Launch macOS.command`
   - Windows: `Launch Windows.cmd`
   - Linux: `bash "Launch Linux.sh"`.
4. For T-Mobile Wholesale, choose **14**, leave the SIM slot blank for automatic selection, inspect the read-only preview and type `yes` to install. For the existing Russia/Belarus IMSI workflow, choose **1** instead.

Keep the iPhone unlocked until the operation finishes. If iCloud Backup is enabled, the phone is charging (a computer cable counts) and the screen goes dark, iOS starts a backup roughly once a day and rewrites Books support files—even in the middle of a stage ([#44](https://github.com/ios-bundles/CarrierSIM/issues/44)). The script warns before stages and shows the time of the last backup.

On first launch, the script downloads its dependencies into `.venv`. This takes a few minutes.

### Without Python: standalone builds

Releases include `CarrierSIM-vN-macOS-arm64.zip`, `CarrierSIM-vN-macOS-x86_64.zip`, `CarrierSIM-vN-Windows-x64.zip` and `CarrierSIM-vN-Linux-x64.zip`. The Python version for all three OSes is `CarrierSIM-vN-Python-universal.zip`. Standalone builds include Python and dependencies. GitHub Actions (`.github/workflows/standalone.yml`) builds them from the same tag; verify provenance with `gh attestation verify CarrierSIM-vN-….zip -R ios-bundles/CarrierSIM`.

1. Extract the entire archive. `bundle.yaml`, `assets.zip` and the README are beside `CarrierSIM` (`CarrierSIM.exe`); `runs` is created there too.
2. macOS: the build is not Apple-signed. Remove quarantine from the extracted folder once: `xattr -dr com.apple.quarantine ~/Downloads/CarrierSIM` (replace the path with yours), then double-click `CarrierSIM`. Otherwise macOS may block the executable or its libraries.
3. Windows: iTunes x64 is still required; follow the [4PDA instructions](https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362). Run `CarrierSIM.exe`. A 4PDA download returning 404 may require registration, login and some forum activity.
4. Linux: install system `usbmuxd`, extract with `unzip` to preserve symbolic links, and run `./CarrierSIM` in a terminal. Builds are created and checked on Ubuntu 22.04 x64; use the Python version on older systems.

Menus and flags are the same as in the script: `CarrierSIM --status`, `CarrierSIM --bundle Vodafone_ro --sims 1`, and so on. The macOS arm64 build was checked without a phone (`--check`, menu). Windows and Intel Mac builds have not been run on physical hardware.

## Menu

| Item | Action |
| --- | --- |
| **1** | Install using `bundle.yaml` (MTS Russia: Vodafone_ro; other eligible SIMs: Vodafone_hu) |
| **2** | Show SIMs and the installation plan without writing |
| **3** | Check the computer and files; no phone required |
| **4** | Restore normal selection for one SIM or all SIMs by removing IMSI links only |
| **5** | Recover after a failed operation |
| **6** | Show flag help |
| **7** | Choose another bundle and SIM (1, 2 or both): show a plan and bundle details before confirmation |
| **8** | Connection diagnostics: IMS, VoLTE/VoWiFi/VoNR, ePDG, network, 5G SA (log reading only) |
| **9** | Watch a test call: transport (Wi-Fi/LTE/5G) and EVS/AMR codec (log reading only) |
| **10** | Switch normal phone operations between USB and experimental Wi-Fi |
| **11** | Generate a profile report for the forum or an issue: CommCenter log and your manual checks (read-only) |
| **12** | Experimental GID1 override: preview, then explicit confirmation; USB-only |
| **13** | Undo a GID1 override using its run directory: preview, then explicit confirmation; USB-only |
| **14** | Automatically install T-Mobile Wholesale for an eligible SIM; no manual identifiers; USB-only |
| **15** | Remove an owned ICCID-qualified Wholesale installation; no run-directory input; USB-only |

For items **12–15**, the default is **NO**: Enter cancels confirmation. Item **7** keeps its existing blank-answer-means-yes behavior. Items **12–15** refuse Wi-Fi mode; use item **10** to select USB first. Items **14/15** use Enter at the earlier **slot** prompt for automatic selection; an already-installed/no-op preview skips confirmation and writing.

## Automatic T-Mobile Wholesale: items 14 and 15

**A verified installation is left installed. No daemon, scheduled task or connected computer is needed afterward.** The retained qualified installation worked and survived reboot according to the user. This is persistence on the tested build, not a guarantee against iOS updates, carrier-settings updates, SIM replacement or a full device reset. Remove the owned rule before an iOS/carrier update if you need assured access to the current uninstall mechanism.

Run your OS launcher and choose **14**. Enter selects the sole eligible SIM automatically; enter `1` or `2` to choose a slot explicitly. Inspect the preview, then type `y` or `yes` to install. Unsupported or ambiguous selections stop without writing. Menu **15** previews explicit removal and also defaults to **NO**.

From the updated project folder:

```sh
# Read-only plan: no trigger, catalog export, Books backup or phone write
python3 launch.py --tmobile-wholesale --status
# Install automatically and leave the verified rule applied
python3 launch.py --tmobile-wholesale
# Preview and explicitly remove only the owned Wholesale rule
python3 launch.py --remove-tmobile-wholesale --status
python3 launch.py --remove-tmobile-wholesale
```

Use `py` instead of `python3` on Windows. Standalone builds use `CarrierSIM` or `CarrierSIM.exe` instead of `python3 launch.py`. Add `--sims 1` or `--sims 2` if selection is ambiguous. CLI commands without `--status` are write actions, not interactive confirmation prompts. Use a release containing these changes; previously downloaded binaries are not updated by editing the Python source.

The automatic workflow:

- Requires USB and a device-reported `com.apple.TMobile_MVNO_US` profile. Other carriers are not converted. An already reported Wholesale profile is a no-op.
- Verifies a fresh stock carrier **and operator** baseline, then discovers the actual chosen plain `<PLMN>_GID1-<HEX>` key from CommCenter. This is the matched GID1 key/prefix, **not a raw EF GID1 read**. Missing, composite or ambiguous keys stop safely; `69` is not assumed.
- Uses that key plus the selected SIM's **full ICCID**, targeting only the unchanged system `TMobile_Wholesale_US.bundle`. Another enabled SIM on the same PLMN is conservatively refused.
- Backs up the catalog and Books, binds the journal to this phone and SIM identities, reads back the write, verifies Wholesale carrier/operator selections and preserves every other active slot's baseline.
- After completed catalog placement, a connection loss during **readback or the fresh rescan** reconnects to the **same phone**. An interrupted readback stage is repaired first; the complete catalog and SIM identities must still match before carrier/operator verification can succeed. The alias is not reapplied, removal is not repeated, and partial logs are never accepted. Genuine rejection or changed evidence still invokes exact owned-entry rollback.

Re-running installation recognizes a healthy owned installation, including an older manually created qualified journal, and leaves it unchanged. This read-only/no-op path checks recorded verification and the current reported profile; it does **not** perform a fresh catalog export or signature rescan. If an owned installation is interrupted or no longer reported as Wholesale, automatic installation stops rather than overwriting or blindly retrying.

**`--recover` repairs interrupted work; it does not uninstall a healthy installed profile.** Removal is explicit through **15**, `--remove-tmobile-wholesale`, or the printed journal-bound `--undo-gid1` command. The original SIM may be disabled or removed; an unowned rule or stock Wholesale selection is never removed automatically. Item **4** is IMSI-only and is not Wholesale uninstall.

Keep the original **`runs` directory** and all referenced backups when moving/upgrading CarrierSIM; use `--runs PATH` if they are elsewhere. Removal discovers ownership there, not from the displayed carrier name alone. Journals and catalog ZIPs contain the full ICCID even though console/log output masks it: **do not publish these private artifacts**.


## Choosing a bundle: bundle.yaml

The `bundle.yaml` file beside the script sets the bundle for menu item **1**:

```yaml
# Default bundle for Russian (250) and Belarusian (257) SIMs
default: Vodafone_hu
# Bundle for a specific carrier: MCCMNC without spaces (250 01 = MTS RU)
25001: Vodafone_ro
```

For each SIM, an exact MCCMNC entry takes precedence over `default`. Without the file, Vodafone_hu is used. `default` applies only to Russian (MCC 250) and Belarusian (MCC 257) SIMs: foreign SIMs retain their current profile and are marked as untouched in the plan (item **2**). To change a foreign SIM, add its MCCMNC or explicitly select its slot in item **7** (`--sims 1` or `2`); an explicitly selected slot receives the profile regardless of carrier. Use the folder name from `/System/Library/Carrier Bundles/iPhone`, without `.bundle`. Item **7** and `--bundle` override the file for that invocation.

### Bundle details

Items **1**, **2** and **7** show properties from [ios-bundles.github.io](https://ios-bundles.github.io) beneath the plan: home-network VoWiFi priority, roaming VoWiFi, Wi-Fi Calling network label, iMessage/FaceTime activation number, LTE/4G label, VoLTE switch, 5G, EVS, IMS supplementary services (XCAP) and visual voicemail. For example, Vodafone_tr uses the local Turkish activation number 13191, unreachable from Russia; forum users report that activation by phone number may not recover if it is lost. Vodafone_ro disables roaming VoWiFi.

These are properties of iOS 27.0 bundle files for iPhone 17, **not a live phone test**: actual carrier behavior may differ. The table is downloaded weekly into `runs/bundles.json`. Without internet, bundle details are omitted. If a name differs only in case (`mts_ua` rather than `MTS_ua`), the script stops before writing. For an unknown name it suggests similar names and continues, because another iOS version may contain that bundle.

## Experimental GID1 override: items 12 and 13

**Manual expert workflow; acceptance and service depend on the device/carrier.** For supported T-Mobile MVNO-to-Wholesale installation, prefer [automatic item 14](#automatic-t-mobile-wholesale-items-14-and-15). Manual GID1 changes a user-catalog alias, not Apple system bundles, signatures, the matching algorithm or carrier provisioning. A more specific alias can receive a higher native score; writing it alone does not establish target-board acceptance or service availability.

A normal installation matches a full IMSI. The experiment instead creates exactly one alias named `<MCCMNC>_GID1-<HEX>` in the user carrier catalog, pointing to an untouched system bundle through the existing bundle-link mechanism. Optional `--gid1-iccid` appends `_ID-<ICCID>` using the selected SIM's full ICCID. MCCMNC is derived from the selected SIM; GID1 is normalized to uppercase, even-length hexadecimal representing **1–16 bytes**. Supply the GID1 from a reliable source; do not guess it.

**Selecting `--sims 1` or `2` identifies the SIM used to derive the rule and verify the trial; the slot itself does not isolate the rule.** In plain GID1 mode, other present or future SIMs matching the same MCCMNC and GID1 can be affected. ICCID-qualified mode additionally requires the recorded full ICCID. Apply still conservatively refuses another enabled SIM on the same PLMN. Item **4** removes IMSI links only and **does not remove GID rules**.

### Observed plain-GID1 trial — 2026-10-13

On an iPhone 18 Pro (`iPhone19,2`, board `V63AP`) running iOS **27.0.1 (24A446)**, the tool wrote and read back `310240_GID1-69 → TMobile_Wholesale_US.bundle`. CommCenter recognized the user alias, but reported this candidate order:

| Candidate for `310240_GID1-69` | Match score | Preference and result |
|---|---:|---|
| System → `TMobile_MVNO_US.bundle` | 70.60 | First; selected for SIM2 |
| User → `TMobile_Wholesale_US.bundle` | 70.60 | Second; not selected |

SIM1 remained on `Verizon_LTE_US.bundle`. The failed target-selection check triggered automatic scoped undo. The alias was removed, final catalog readback matched the backed-up baseline, both active slots returned verified baseline selections, and all trial transfer journals reported Books restored with no recovery required. The owner journal is `runs/20261013-174048-gid-20a270/gid-alias.json`, with terminal state `rolled_back`.

The plain-GID1 trial failed to change selection; it did not demonstrate a Wholesale signature failure. Wholesale was not selected in that trial, so its target-board signature acceptance and service/reboot behavior were not tested. The same-key alias is not a working override on this tested device/build.


### Observed ICCID-qualified trial — 2026-10-13

On the same device/build, `--gid1-iccid` wrote and read back `310240_GID1-69_ID-<iccid> → TMobile_Wholesale_US.bundle`. The captured CommCenter log reported:

| Request | Stock MVNO score | Qualified Wholesale score |
|---|---:|---:|
| Carrier/operator matching | 70.60 | 80.60 |
| Carrier lookup | 76.00 | 86.00 |

Both SIM2 links (`Carrier2Bundle.bundle` and `Operator2Bundle.bundle`) resolved to the untouched system `TMobile_Wholesale_US.bundle`, with `Verification Skipped: No` and `Verification Result: Success`. This is observed selection and signature acceptance on this build, not proof of calls/data, VoWiFi/VoLTE or persistence after reboot. The later lookup included a private GID2 field; it did not expose a usable value for another rule.

The connection dropped during log capture. Although trigger installation completed, the incomplete capture was correctly refused: the apply did **not** reach `applied_verified`. The first automatic rollback also lost its connection. A USB-reconnected, journal-bound undo recovered the interrupted transfer and finished with a fresh, complete rescan. Its first verification failed, but its scoped recovery succeeded; the command still exited with the original capture error.

Final owner state: **`undone`** in `runs/20261013-183644-gid-d8ed8e/gid-alias.json`. The qualified alias was removed, the final catalog readback equaled the pre-trial backup, and both carrier and operator selections returned to `Verizon_LTE_US.bundle` for SIM1 and `TMobile_MVNO_US.bundle` for SIM2. All nine transfer journals reported Books restored, with no active trial recovery remaining. The stock bundle and matching algorithm were not modified. Logs and private recovery artifacts are retained in that run and its recorded recovery directories.


### Retained service trial — 2026-10-13

An explicitly requested service trial left the same qualified Wholesale rule installed on SIM2. Unified logging avoided legacy replay, but the first carrier switch still closed the device session. The default failure handler removed the catalog rule before its Books cleanup lost the connection; Wholesale remained cached as active. Fresh-session recovery repaired only the consumed transfer's Books cleanup, and a catalog snapshot proved the rule was absent before its exact owned entry was restored.

The retained installation then passed catalog readback, stable SIM identity checks and a fresh, complete rescan: **both carrier and operator selected `TMobile_Wholesale_US.bundle` for SIM2**, with successful signature verification. SIM1 remained on verified `Verizon_LTE_US.bundle` selections. The phone reported Wholesale version **72.0**. Books cleanup was verified and no transfer recovery remained pending. The user subsequently reported that it works and persists through reboot; no individual unreported service checks are inferred.

The owner is `runs/20261013-190456-gid-1fa4b6/gid-alias.json`, **`applied_verified`**, with verification evidence in `runs/20261013-191437-gid-retain-37f2b3/retained-rescan`. No undo was run after the retained installation. The implementation now reconnects and verifies in place after a qualifying connection loss, before deciding whether scoped rollback is necessary. Generic `--recover` leaves healthy installed journals alone.

On the tested Mac, from the repository directory, undo **only when explicitly wanted**:

```sh
.venv/bin/python3.12 carrier.py --undo-gid1 "runs/20261013-190456-gid-1fa4b6"
```


### Plan, apply, test, undo

Use USB for the entire experiment. Keep the phone unlocked and preserve its run directory.

```sh
# Read-only plan; explicit bundle and exactly one slot are mandatory
python3 carrier.py --gid1 HEX --bundle NAME --sims 1 --status
# Apply once (replace HEX and NAME with your actual values)
python3 carrier.py --gid1 HEX --bundle NAME --sims 1
# Read-only undo preview for the exact apply run
python3 carrier.py --undo-gid1 "runs/RUN_DIRECTORY" --status
# Remove the owned alias and verify undo
python3 carrier.py --undo-gid1 "runs/RUN_DIRECTORY"
```

Use `--sims 2` for slot 2. On Windows, replace `python3` with `py`; standalone builds use `CarrierSIM` or `CarrierSIM.exe` instead of `python3 carrier.py`. Alternatively, choose item **12**, inspect its preview and type `y` or `yes` to apply; use item **13** with the literal saved run directory to undo. Empty inputs and Enter at either confirmation cancel. If item **10** currently selects Wi-Fi, items **12** and **13** refuse before collecting input: switch item **10** to USB first. CLI apply/undo commands themselves are write actions; adding `--status` makes them read-only planning.

The manual and automatic GID/T-Mobile action flags are mutually exclusive and cannot be combined with other write, recovery, diagnostic or check actions. `--status` is allowed. Do not add `--wifi`; apply and undo are USB-only, even when the launcher previously selected Wi-Fi. There is **no automatic multi-apply retry**.

#### ICCID-qualified trial (CLI)

Add `--gid1-iccid` to the plan and apply commands:

```sh
python3 carrier.py --gid1 HEX --gid1-iccid --bundle NAME --sims 2 --status
python3 carrier.py --gid1 HEX --gid1-iccid --bundle NAME --sims 2
```

The qualifier uses the full ICCID reported for the selected SIM, not guessed bytes or another SIM's identifiers. This narrows the rule to that ICCID; it does **not** promise a higher matching score or target acceptance. A fresh baseline must still choose the plain `<MCCMNC>_GID1-<HEX>` key. The qualified trial requires successful, consistent **carrier and operator** selections for every enabled slot: both target selections must resolve to the requested system bundle, and other slots must remain at their corresponding baselines. Undo verifies both layers for unchanged original SIM identities.

`--gid1-iccid` requires `--gid1`; do not pass it to undo. The saved journal determines the qualifier and validates its ICCID against the original selected-SIM hash, including when that SIM is removed. Menu **12** remains plain GID1; menu **13** can undo either form using its exact run directory. CLI output and CommCenter logs mask the ICCID, but the exact alias name in the recovery journal and catalog archives contains it. **Keep these artifacts private.**


The read-only preview does not install a trigger, export the catalog or create a Books backup. It is not proof that an alias is absent or a bundle will be accepted: those checks occur during the confirmed write operation.

Before committing the alias, the experiment requires a fresh carrier-catalog and Books backup, device/family binding, stable hashed IMSI and ICCID bindings for the selected SIM and all other enabled SIMs, an absent alias, exact symlink bytes, saved baseline slot selections, backup hashes and durable lifecycle state in `gid-alias.json`. A fresh baseline rescan must show that the selected SIM chose the **exact `<MCCMNC>_GID1-<HEX>` matching key**, with verified baseline selections; a resolved bundle name or selection score alone is not enough. Identities are rechecked before writing. Existing aliases, unsupported families, known-closed AirLift builds, missing or unstable identifiers, and missing selection evidence cause a safe stop—not a guessed fallback. Rescan requires an independent, non-overlapping, device-board-signed trigger. Successful trigger installation alone does not prove target-bundle acceptance.

Trigger rescans capture the live unified CommCenter stream, the same logging service used by diagnostics. Historical entries are disabled, collection starts after fresh CommCenter activity is observed, and identifiers are masked before disk. This avoids the legacy relay's buffered-line replay without accepting old selections as fresh proof. A dropped stream invalidates that capture. After completed catalog write/readback, an actual connection loss permits a same-device reconnect, catalog/identity checks and one fresh verification-only rescan; partial logs alone are never success.

Selection is proved only by a target `Resolved path`, the correct slot link and successful verification; other enabled slots must remain at their baseline selections. Rejection, unverified selection or missing logs cause automatic **scoped undo**: remove only the exact owned alias from the current catalog, preserve unrelated changes, rescan, and verify rollback. Missing catalog, Books or recovery verification must not be reported as completed rollback.

A successful apply **remains installed** for normal use and service/reboot checks. Record the run directory and the exact undo command printed by the tool. Test the calls, data, VoWiFi/VoLTE and other services you rely on; profile selection does not prove them. Undo is explicit, not an immediate post-install step. **Remove the rule before an iOS/carrier-settings update if you need guaranteed access to today's removal mechanism.** A future update can replace the rule or close that write path.

Undo uses the operation's saved `gid-alias.json`, device binding and exact owned-entry checks. It remains possible if the original SIM is disabled or removed. A changed alias is refused; an already absent alias is an idempotent undo only with verification. Exceptions, disconnections, interrupts and process-crash journals preserve recovery state: reconnect by USB and use the recorded undo/recovery instructions, without blindly applying again. Item **5** (`--recover`) performs bound undo for **interrupted** GID journals, never another apply, while leaving healthy `applied_verified` installations installed. Existing stage recovery remains available for interrupted transfers or Books cleanup. Remove a healthy installation explicitly through item **13**, item **15** for owned qualified Wholesale, or the printed `--undo-gid1` command.

Undo reports the SIMs present at final verification, not an earlier preview. With no active SIMs, it verifies the catalog and Books restoration but explicitly reports slot selection as untested; enable a SIM before checking its selection or service. A crash before any device-change intent can be cancelled without a trigger. Once a final transfer was authorized, an unconsumed final source remains ambiguous: recovery stops rather than blindly replacing the catalog. Stage repair also refuses to overwrite a different current catalog. An identical alias created by another operation before our final transfer was authorized is not owned and is preserved.

`--restore-backup "runs/RUN_DIRECTORY"` is a fallback that restores the **entire saved catalog**, not a scoped alias removal. It can overwrite unrelated catalog changes made after the backup. Preserve backups and journals until verified undo and recovery are complete. Do not publish them; catalog archives can contain SIM identifiers even when the experiment's identity record stores hashes.

### Inspecting a retained eSIM trial

1. Open **Settings → General → About**, scroll to the relevant eSIM section, and inspect **Carrier** and its version. The displayed carrier name/version need not visibly change; About alone does not identify the selected bundle.
2. Open **Settings → Cellular / Mobile Service → the trial eSIM**. Inspect **Voice & Data** and **Wi-Fi Calling** and note which options are available. Dismiss any carrier-settings update prompt while the experiment is installed.

For service checks, explicitly select that eSIM for cellular data and outgoing calls. Turn off Wi-Fi for the cellular-data check and prevent automatic data switching to another SIM from hiding a failure. For Wi-Fi Calling, use the Airplane Mode/Wi-Fi procedure below and make an actual call; a carrier label alone does not prove service. Preserve the apply run and its exact undo command. A successful apply is left installed until you explicitly undo it.


## After installation: enable VoWiFi

For the normal IMSI profile, a reboot after installation is recommended so the profile takes full effect. Then check connectivity, calls and data. For automatic Wholesale, use the workflow and persistence caveats above; manual GID1 users should perform their own service checks and undo explicitly when wanted.

1. Settings → Cellular → the relevant SIM → enable Wi-Fi Calling.
2. Enable Airplane Mode, then enable Wi-Fi.
3. Wait for the Wi-Fi indication beside the carrier name.
4. Disable Airplane Mode. VoWiFi should then connect automatically if the network and carrier allow it.

## Connection diagnostics: items 8 and 9

Item **8** (`--diagnose`) reads CommCenter logs for up to 90 seconds. Enable Airplane Mode, disable it after 10 seconds and leave Wi-Fi enabled. The USB log stream may disconnect; the script reconnects. Once all SIMs re-register with IMS after Airplane Mode is disabled, it waits another 10 seconds and finishes. Press Enter in a terminal to finish earlier. The report shows IMS, VoWiFi, network and 5G SA per SIM, plus the VoWiFi tunnel's ePDG address, latest IKE state and latest IKE error, if any. An ePDG address of `127.0.0.1` or `0.0.0.0` cannot reach the carrier with any bundle. DNS blocking by a router, ad blocker (AdGuard, Pi-hole), VPN or carrier is a common cause. Full CommCenter logs, including IKE details, are saved in `runs/DATE-diagnose/commcenter.log` with numbers masked.

Item **9** (`--watch-call`) listens for up to 180 seconds while you make a test call and shows its transport and codec. Press Enter after the call to finish.

## Profile report: item 11

User reports, rather than bundle-property tables, establish which profiles work best for a carrier. Item **11** (`--report`) collects them in a common format:

1. Read CommCenter logs for 90 seconds, as in item **8**. Enable Airplane Mode, disable it after 10 seconds and leave Wi-Fi enabled.
2. For each SIM, answer which checks you performed: VoWiFi calls in Airplane Mode, automatic VoWiFi activation, staying on 4G/5G during calls without Wi-Fi (VoLTE), the 5G band from `*3001#12345#*`, iMessage by phone number, SMS over Wi-Fi, tethering and merging calls. Answer `y` (yes), `n` (no) or Enter (not tested). Questions are terminal-only; without a terminal they are skipped and the report notes that manual checks were not supplied.
3. Print the block between `===== Profile report` and `===== End of report` and save it in `runs/DATE-report/report.txt`.

The report includes model, iOS, carrier, loaded profile and version, log summary and your answers. Phone numbers, IMSI and ICCID are not included. Paste the block into the forum or the **Profile report** issue template. The phone is not modified.

## Frequently asked questions after installation

**Will a profile survive an iOS update or reboot?** The qualified Wholesale user reported a working installation that survives reboot; this does not establish survival across future iOS/carrier updates. Forum users separately report that ordinary IMSI profiles survived updates from 27.0 to 27.0.1 and reboots. In iOS 27.2 beta 3, Apple closed the write mechanism ([#56](https://github.com/ios-bundles/CarrierSIM/issues/56)): AirTraffic no longer moves files into the carrier catalog. An existing profile can remain after an update, but the script cannot install, change or remove it on that version. Writing still works on 27.2 beta 1 and 2. The normal IMSI workflow warns on newer versions and attempts the transfer; refusal stops it before changes. If you will need to change a profile, do not update to a known-closed build. Ordinary IMSI bindings can stop matching after SIM replacement, or be removed by a full iPhone reset; Reset Network Settings does not remove them. **Remove an owned GID/Wholesale rule before an iOS/carrier update if you need assured access to its current removal path.**

**I changed SIMs and the profile disappeared.** Ordinary profiles are bound to IMSI. A new SIM, reissued eSIM or number transfer from eSIM to physical SIM produces a new IMSI; install again with item **1** or **7**.

**iMessage and FaceTime do not activate after changing SIMs.** Forum users also see this with the normal bundle; item **4** does not resolve it. Several users fixed it by getting a replacement SIM/eSIM: the new ICCID activated in 10–60 minutes. Install the profile again afterward because IMSI also changes. A carrier may restrict incoming and outgoing SMS for the first 24 hours after replacement; phone-number activation may be unavailable until those restrictions expire. Wait and retry activation.

**There is a 5G icon, but speed is like LTE.** The icon and switch do not prove a 5G connection. Dial `*3001#12345#` to open Field Test. Bands beginning with n (n1, n7, n40) are 5G NR; bands beginning with b are LTE. NSA is 5G over LTE: if only b-bands appear, the phone is using LTE. n78 and n79 are unavailable in Russia with any bundle: the signed GRI file in `Default.bundle` sets allowed bands and is not modified by the script.

**The VoLTE switch disappeared.** Many bundles, including Vodafone_hu, Vodafone_ro and Vodafone_tr, omit the switch and leave VoLTE always enabled. Turn off Wi-Fi and make a call: if the network does not fall back to 3G and data still works during the call, VoLTE works.

**EVS (HD Voice+) does not activate.** Both parties must support EVS and use IMS (VoLTE, VoNR or VoWiFi). Short codes and support lines almost always use AMR, so calls to 611 or 0890 are not useful EVS tests; call an EVS-capable subscriber on the same carrier. If outgoing calls use EVS but incoming calls use AMR-WB, reboot: the author's observation was that bundle EVS settings applied only after a full reboot, not Airplane Mode or reinstallation. Item **9** shows the codec.

**Data is slower or MMS no longer sends after installation.** The bundle also changes cellular-data settings and may supply the donor carrier's APN. Open Settings → Cellular → [line] → Cellular Data Network. If the APN is foreign, enter your own carrier's APN and MMS settings from its website, then toggle Airplane Mode. If the menu is absent or the APN is already correct, coverage is more likely: some forum users saw slower speeds with the same bundle while others did not, and comparisons around Moscow varied by cell site.

**VoWiFi works only in Airplane Mode or with a weak signal.** The bundle and carrier decide when to use Wi-Fi rather than cellular service. Some bundles prefer cellular on the home network. Compare them at [ios-bundles.github.io](https://ios-bundles.github.io).

**The carrier line says “Vodafone TR Wi-Fi” or “4G” instead of “LTE”.** The bundle sets the Wi-Fi Calling label and network icon. This does not affect connectivity, and they cannot be changed without changing bundles because bundles are Apple-signed.

**The phone offers a carrier settings update.** One forum user accepted an update and partially lost the profile: 5G disappeared after installing with v3. Another user with Vodafone_tr receives the offer daily; a third with the same bundle never receives it. There is not enough verified evidence, so declining is safer. If an ordinary IMSI profile is lost, reinstall with item **1** or **7** and report the case in [issue #16](https://github.com/ios-bundles/CarrierSIM/issues/16). Undo a GID1 trial before any such update.

**MegaFon: SMS over VoWiFi.** Wi-Fi calls work, but SMS does not work for everyone. Before October 2, 2026, no forum user reported successful MegaFon SMS over VoWiFi. On October 2, two Vodafone_hu users sent SMS in Airplane Mode; a third using the same bundle could not. Test a normal phone number: even the normal bundle does not send short-code SMS over VoWiFi. Beeline users report SMS over VoWiFi working.

**Beeline: the Wi-Fi Calling switch exists, but VoWiFi does not work.** First check that the service is enabled on your number. Forum users report that Beeline disables it for a SIM used in a phone without official VoWiFi support. Either ask support to re-enable it (they sometimes refuse), or put the SIM in an Android phone or iPhone 13 or earlier, enable VoWiFi there, then return it to the iPhone.

**T2 and VoWiFi.** Before October 1, 2026, forum users could not make T2 VoWiFi work on iPhone with any bundle except Nova_is plus an ePDG override in their DNS, and even that did not work for everyone. Overnight on October 2, it began working automatically, without DNS changes or reinstallation, in Moscow, Saint Petersburg, Nizhny Novgorod, Volgograd, Omsk, Bryansk and Kaluga Region, using Vodafone_hu, MTS_ua and Telia_az. Users in Perm and parts of Moscow still reported failure; changing bundles did not help. T2 appears to be enabling the service region by region: wait and occasionally toggle Airplane Mode. The forum also cites T2's website: VoWiFi is incompatible with “City number”, “Second number”, “Corporate PBX”, CVPN and CSD. A bundle change will not fix those service conflicts.

## How it works

CommCenter selects the carrier bundle for a SIM. Bundles reside in two places:

- `/System/Library/Carrier Bundles/iPhone/*.bundle`: Apple-signed system bundles on a read-only partition;
- `/var/mobile/Library/Carrier Bundles/iPhone`: the user catalog, containing carrier updates (IPCC) and symlinks used by CommCenter to find a SIM's bundle.

The script changes only the user catalog. For iPad, the family-specific catalog and system bundle paths are used.

The computer connects by USB, or by local network in Wi-Fi mode, to services made available after Trust:

- **AFC** (Apple File Conduit, `com.apple.afc`): file access within `/var/mobile/Media` only. Photo import applications and iPhone file managers use it; it cannot access `/var/mobile/Library` or `/System`.
- **`installation_proxy`**: installs applications and carrier IPCC packages.
- **`syslog_relay` / unified OS trace**: diagnostic logs and fresh structured CommCenter evidence; GID rescans use unified logging with historical entries disabled.
- **`streaming_zip_conduit`**: uploads and extracts a ZIP into `/var/mobile/Media`.
- **AirTraffic** (`com.apple.atc`): iTunes/Finder media synchronization.

The first four use `pymobiledevice3`; AirTraffic uses the Apple library on macOS and Windows, and the native backend on Linux.

Normal IMSI installation steps:

1. **Trigger.** Install another carrier's signed IPCC through `installation_proxy` (`AVEA_tr`, `Swisscom_ch` or `O2_Germany`, chosen not to overlap your SIMs). IPCC installation forces CommCenter to reselect bundles and creates the user catalog if needed. The trigger bundle remains there but does not match your SIMs.
2. **Backup.** Move the catalog into `/var/mobile/Media`, read it over AFC and save `original.zip` in the run directory.
3. **Write.** Add root-level symlinks named with each selected SIM's 15-digit IMSI, pointing to `/System/Library/Carrier Bundles/iPhone/Vodafone_hu.bundle` or the bundle selected in item **7**. The rest of the catalog is unchanged.
4. **Verify.** Read the catalog back and compare it with the plan. Install the trigger again and inspect CommCenter's `Resolved path` and `Verification Result` for each SIM's selected bundle and accepted signature. If the bundle chosen in item **7** is not selected for any SIM (for example, it does not exist in that iOS), restore the catalog backup.

AFC cannot write the catalog because it is outside `/var/mobile/Media`. The AirLift method uploads an archive into Media through `streaming_zip_conduit`; AirTraffic then moves it into place using a relative path during a synthetic Books sync. The script removes AirTraffic's support files in `/var/mobile/Media/Books` and compares Books with its backup. macOS and Windows use Apple's `AirTrafficHost` library. Linux implements `com.apple.atc` through `pymobiledevice3`, based on [AirCard-Linux](https://github.com/shinkuan/AirCard-Linux) and [its variant](https://github.com/hoicau/AirCard-Linux).

On Linux, `bash "Launch Linux.sh" --atc-probe` checks AirTraffic session startup without transferring files. Full USB installation has been tested on iPhone 14, 14 Pro, 15 Pro, 15 Pro Max, 17 and 17 Pro with iOS 18.7.7 and 26.x. Restoring both SIMs' normal profiles and then installing only for SIM 1 was tested on iPhone 14 Pro. On iPhone 15 Pro / iOS 27.0.1 (24A446), MTS, USB recovery (item **5**) followed by normal-profile restoration (item **4**) was tested: status confirmed MTS_ru and Books support files were restored. Linux Wi-Fi has not been separately tested.

Verified USB installation combinations:

| iPhone | iOS (build) | SIM → bundle |
| --- | --- | --- |
| 14 | 26.3 (23D127) | MTS → Vodafone_ro |
| 14 Pro | 26.3.1 (23D8133) | Yota and T2 → Vodafone_hu |
| 15 Pro | 18.7.7 (22H340) | Beeline → Vodafone_hu |
| 15 Pro Max | 26.6.1 (23G83) | Beeline → Vodafone_hu |
| 17 | 26.6.1 (23G83) | MTS → Vodafone_ro |
| 17 Pro | 26.6.2 (23G90) | MTS eSIM → Vodafone_ro |

A VoWiFi call was also confirmed on iPhone 17. These tests were performed on Linux and concern the existing IMSI workflow, not the GID1 experiment.

Apple calls and AirTraffic exchange are isolated in `airtraffic_apple.py`, running in a separate process. `carrier.py` records logs and confirms the backup before the final transfer. Importing the module does not load Apple libraries.

Item **4** removes root-level 15-digit IMSI symlinks (or the selected SIM's link). Other entries, including MCCMNC links, GID1 aliases and IPCC packages, are left intact.

## If something goes wrong

- **Previous operation unfinished:** use item **5**, then repeat the intended normal action. For an experimental alias, preserve the run directory and follow its undo/recovery instructions; do not blindly apply again.
- **Restore normal bundle selection:** item **4** removes IMSI links added by the script. Use item **13** for GID1 aliases.
- **Log disconnected in items 8 or 9:** Airplane Mode can close the stream. The script reconnects and continues until the time limit, then reports the reconnect count. If logs do not return for more than 20 seconds (Wi-Fi Airplane Mode can break the entire connection), the report uses the data collected so far. If no log data arrived at all, such as after cable removal, collection fails.
- **Selection not confirmed (exit code 3), normal IMSI workflow:** each SIM is reported as having a different bundle selected, a rejected signature or no bundle selection. If iOS selected another bundle, the script restores the previous settings. If the rescan log is empty, the normal workflow leaves the written catalog in place: enable Airplane Mode for 15 seconds and check the current-bundle line in item **2**. **The GID1 experiment differs: missing/unverified selection triggers scoped undo, not leaving an unproved alias installed.**
- **`runs` folder:** contains catalog backups and operation logs. Do not delete it during installation or before verified GID1 undo/recovery. Ordinary completed-run leftovers do not need migrating to a new version; the script cleans its own device leftovers. Preserve active GID/Wholesale owner journals and referenced backups when upgrading the software; use `--runs PATH` to keep access to their original location. Before updating iOS/carrier settings, use explicit removal if you need the current write path. Do not publish `runs`: backups and qualified journals contain SIM/device identifiers. Default `runs` is excluded in `.gitignore`. If `--runs` uses another directory inside a clone, exclude it yourself, preferably in `.git/info/exclude` (for example `/wifi-runs/` for `--runs wifi-runs`). The script does not alter git rules.
- **CarrierSIM version:** appears in the menu, at the start of `session.log`, in `environment.json` and in diagnostics. `-dev` denotes a development version. The script-build value is the first 12 characters of `carrier.py`'s SHA256, distinguishing changes within a version. `python carrier.py --version` requires no phone.

**macOS says “Launch macOS.command is damaged… Move to Trash”.** This can be macOS blocking an unsigned download rather than a damaged file.

1. Click **Cancel**, not Move to Trash.
2. Open Terminal, type `bash ` with a trailing space, drag `Launch macOS.command` into the window and press Enter.

To enable double-click launching, remove quarantine from the extracted folder once:

```bash
/usr/bin/xattr -r -s -d com.apple.quarantine "$HOME/Downloads/CarrierSIM"
```

**Windows cannot detect the iPhone.** If Explorer shows `Apple iPhone` and photos but iTunes and the script do not detect it, the Apple Mobile Device USB driver may be missing. Reinstalling iTunes does not always help. Download “Apple Mobile Device USB Driver” from [Microsoft Update Catalog](https://www.catalog.update.microsoft.com/Search.aspx?q=Apple%20Mobile%20Device%20USB%20Driver), extract the `.cab`, run `pnputil /add-driver usbaapl64.inf /install` in an administrator command prompt in that folder, then restart the computer.

**“Grappa session could not be established”.** The iPhone rejected AirTraffic exchange. On Windows, `ATHostConnectionSendSyncRequest` could cause this even while backing up the original catalog. The script now uses `ATHostConnectionCreateWithLibrary` and sends `RequestingSync` with prepared Grappa data. Successful AirTraffic exchange does not by itself prove VoWiFi, EVS or 5G functionality.

If the error remains, the script reports that the iPhone rejected the Apple components on this computer and does not retry the same operation. Remove iTunes (including the Microsoft Store version, if present), install full iTunes x64 using the [4PDA instructions](https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362), launch it once, then try again. Use the full installer, not just `AppleMobileDeviceSupport64.msi`. In one case, only version 12.11.0.26 worked, and the old iTunes media library had to be removed before first launch to avoid an iTunes error. A 4PDA download returning 404 may require registration, login and some forum activity.

**InstallProhibited:** Screen Time may prohibit installation: Settings → Screen Time → Content & Privacy Restrictions → iTunes & App Store Purchases → Installing Apps. Allow it temporarily. Managed (MDM) phones can impose the same restriction.

**The iPhone refuses changes to Books/Books.plist while script entries remain.** A known cause is iCloud Backup starting during a stage ([#44](https://github.com/ios-bundles/CarrierSIM/issues/44)). Preserve `runs` and its backups. The following sequence helped in one case:

1. Disconnect the iPhone from the computer.
2. Close all iPhone apps by swiping them away in the app switcher.
3. Force restart: quickly press and release Volume Up, then Volume Down, then hold the side button until the Apple logo appears ([Apple instructions](https://support.apple.com/guide/iphone/force-restart-iphone-iph8903c3ee6/ios)).
4. Unlock after startup and reconnect to the computer.
5. In the same CarrierSIM folder, retry item **5** (`--recover`). If it fails again, send the error text and session log to the author.

**Books support files keep changing:** close Books, wait for book downloads to finish, then retry.

**The script cannot run from this folder:** copy CarrierSIM into Downloads and launch it there.

## Without a cable: Wi-Fi (experimental)

Use item **10** or `--wifi` for normal IMSI actions. The same services run over the network through usbmuxd, like Finder Wi-Fi sync. Full Wi-Fi installation was tested on iPhone 16 Pro Max / iOS 27.0.1; it works but is slower than USB. **GID1 apply/undo are excluded and remain USB-only.**

If AirTraffic does not connect within 170 seconds over Wi-Fi but works immediately over USB, check macOS Local Network permission for Terminal (System Settings → Privacy & Security → Local Network). AirTraffic connects directly to the iPhone; macOS silently blocks that connection without permission. Launch `Launch macOS.command` (or standalone `CarrierSIM`) from Finder or a normal terminal window, not tmux, screen or SSH. For this failure the script rolls back once and does not repeat Wi-Fi attempts. Connect USB, switch item **10** to USB and retry. If Wi-Fi rollback failed, use item **5** over USB first.

Wi-Fi cannot establish Trust: without an existing pairing the script asks for USB. The network must allow Bonjour; guest networks and client isolation will not work.

On macOS, status/log reading through system usbmuxd can work without Local Network permission, but the direct AirTraffic transfer cannot. The script checks before installation. If macOS displays the permission prompt, click Allow; the script waits up to 20 seconds. Without permission it stops with guidance. Recovery checks permission only before restoring the carrier catalog; Books-only cleanup does not require it. macOS does not prompt from tmux, screen or SSH and denies access there.

### How to test Wi-Fi

**One-time preparation over USB**

1. Connect USB, unlock and tap Trust.
2. Run menu item **2**. If SIMs appear, pairing works.
3. Finder → iPhone sidebar → General → enable “Show this iPhone when on Wi-Fi” → Apply. On Windows, use iTunes → Summary → Options.
4. Close Finder/iTunes and disconnect USB.

**Connection test (no writes)**

5. Put the phone and computer on the same non-guest Wi-Fi without client isolation. Keep the phone unlocked and awake.
6. Choose **10** (Wi-Fi), then **2**.
7. A SIM list and plan prove Wi-Fi connectivity. If the phone is not visible, check step 3, sleep and Bonjour. If Trust is missing, repeat steps 1–2.

**Normal IMSI installation**

8. Keep USB nearby. Choose **10** → Wi-Fi, then **1**.
9. It takes longer than USB. If unstable networking causes an AirTraffic failure, connect USB and retry in USB mode.
10. The script also rolls back over Wi-Fi after failure. If the phone sleeps or disappears mid-write, the stage times out after 5 minutes and rollback follows. If rollback fails, connect USB, choose **10** → USB, then **5**.
11. After completion, test VoWiFi as described above.

Logs are in `runs`; `host.jsonl` contains AirTraffic responses. Include relevant logs with a report. Phone logs (`commcenter.log`, `device.log`) replace SIM IMSI/ICCID with `<imsi>`/`<iccid>`, other long numbers and phone numbers with `<num>`, and the device name with `iPhone`. Older logs did not have this masking. ZIP archives (catalog backups and `media-leftovers`) are not masked and contain IMSI; do not publish them.

## Advanced usage

```sh
python3 carrier.py --status                         # SIMs and plan, no writes
python3 carrier.py --wifi --status                  # same over Wi-Fi
python3 carrier.py                                  # bundle.yaml for eligible SIMs
python3 carrier.py --bundle O2_Germany --sims 1       # another bundle, SIM 1 only
python3 carrier.py --restore                        # remove IMSI overrides
python3 carrier.py --restore --sims 2                # SIM 2 IMSI override only
python3 carrier.py --help                           # all flags
```

On Windows use `py` instead of `python3`. See [README.txt](README.txt) for technical details, source URLs and checksums.

**Microsoft Store iTunes libraries:** for a nonstandard installation, specify the library folder with `py carrier.py --check --apple-dir "C:\AppleRuntime"`; use the same `--apple-dir` when installing. Find the package with PowerShell: `Get-AppxPackage AppleInc.iTunes | Select-Object -ExpandProperty InstallLocation`. Direct loading from `WindowsApps` may fail with `WinError 5`; copy the root DLLs, `CoreFoundation.resources` and DLLs from `AMDS64` into a separate folder such as `C:\AppleRuntime`. Keep all files from one version. This chooses AirTraffic libraries; a working Apple Mobile Device service and driver are also required.

## Local changes

- Added USB-only automatic T-Mobile Wholesale installation/removal: discover the actual matched GID1 key, bind the alias to the full selected ICCID, verify carrier/operator selections, and leave a successful installation applied. Recognizes older qualified owner journals without rewriting them.
- Switched user-facing code and shipped documentation to English; launchers are now `Launch macOS.command`, `Launch Windows.cmd` and `Launch Linux.sh`. Existing IMSI behavior and historical run artifacts are unchanged.
- Fixed carrier-switch connection-loss verification to reconnect without reapplying the alias; generic recovery no longer uninstalls healthy profiles. Added regressions for automatic selection, real-key discovery, idempotence, scoped removal, ownership/identity conflicts and interrupted verification. Initial verification: **172 tests run, 2 skipped; suite passed**. Six isolated actual CLI/menu scenarios exercised cancellation, installation, reconnect, repeat/no-op, healthy recovery and explicit removal. The earlier manual qualified installation had live signature evidence and user-reported working/reboot persistence.
- Fixed connection loss before the first post-apply readback, including readbacks interrupted after staging began and the analogous removal path. AFC response futures are observed when the pinned SDK releases them, preserving the original transport error without orphaned-future warnings. **177 tests run, 2 skipped; suite passed**, including before/after reproductions of the readback failure and SDK send/receive race. Actual CLI smoke covered install, repeat/no-op and removal with injected disconnects.
- Physical verification of the readback fix: recovered the failed installation and interrupted Books stage, then completed a fresh automatic installation despite a live rescan connection drop. **SIM 1: T-Mobile Wholesale 72.0; SIM 2: AT&T MVNO 72.0 unchanged**, with carrier/operator signatures verified for both. A read-only follow-up confirmed unchanged phone/SIM identities, all four native stages restored, live Books sync files matching their backup and zero unfinished operations. Re-running installation was a no-op; Wholesale is left applied. Calls, data and reboot persistence were not re-tested in this session.

## License

CarrierSIM's own code is distributed under [MIT](LICENSE). Copyright © 2026 Vladimir B / vlw and contributors. Preserve the copyright notice and license text when distributing copies or substantial portions of the code.

Third-party code retains its licenses. CarrierSIM's license does not apply to Apple files, including IPCC triggers in `assets.zip`, system carrier bundles and Apple libraries; those belong to their respective rights holders.

The transfer protocol is based on [AirLift](https://github.com/0xjohnnydev/airlift) (MIT; [LICENSE-AirLift.txt](LICENSE-AirLift.txt)). Windows Grappa exchange is adapted from [AirCard-Windows](https://github.com/dhava-gautama/AirCard-Windows/blob/e0eadb0c88da55516f1c9f939fd4ce27560910d5/src/airtraffic.rs); its MIT license is in [LICENSE-AirCard.txt](LICENSE-AirCard.txt). Its prepared Grappa data came from `yinyajiang/go-tunes`.

## Automated checks

The **Tests** workflow runs on branch commits and pull requests. It checks Python **3.11–3.14** on Windows, Linux, macOS **15** (Intel), macOS **26** (Apple Silicon and Intel) and macOS **27** (Apple Silicon, GitHub's public-preview `xcode-27` image). The **24 combinations** install dependencies through the launcher code, check service imports, compile sources and run regression tests. Actual OS/Python versions and architecture appear in the job log. **Python compatibility** succeeds only when the entire matrix passes and can be required in branch rules.

Tests cover archives/backups, profile configuration, SIM selection/restoration, failures, Books support files and logs. They use temporary files and simulated connection boundaries, never connect to a phone, and do not replace real-iPhone testing.

Local test command without installing dependencies or connecting a phone:

```sh
python -m unittest discover -s tests -v
```
