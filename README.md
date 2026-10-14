# CarrierSIM

[Discussion on 4PDA](https://4pda.to/forum/index.php?showtopic=1055886&view=findpost&p=145261196)

CarrierSIM is a Python program for macOS, Windows and Linux. It changes SIM-to-bundle rules in the iPhone's user carrier catalog without modifying Apple-signed system bundles. The normal workflow selects an existing bundle for Wi-Fi Calling (VoWiFi), EVS and some 5G settings. A separate automatic workflow installs T-Mobile Wholesale for eligible lines currently using `TMobile_MVNO_US`.

## Requirements and setup

- Python **3.11 or later**, preferably **3.12**. Windows requires **64-bit Python**.
- **Windows:** install full desktop iTunes x64 and its Apple Mobile Device service/USB driver using the [4PDA instructions](https://4pda.to/forum/index.php?showtopic=554020&st=3760#entry107393362). Microsoft Store iTunes and Apple Devices may not work. A forum download returning 404 may require registration, login and forum activity.
- **macOS:** no additional Apple software is required.
- **Linux:** install system `usbmuxd`, for example `sudo apt install usbmuxd` on Debian/Ubuntu.
- A USB cable and internet access for the first launch.

Download or clone the Python source and keep the complete project together in a writable folder, including `bundle.yaml` and `assets.zip`. Connect the phone by USB, enable the relevant SIM lines, unlock it and tap **Trust**. Finish or close Finder/iTunes synchronization.

Run the launcher for your OS:

- macOS: `Launch macOS.command`
- Windows: `Launch Windows.cmd`
- Linux: `bash "Launch Linux.sh"`

Or open a terminal in the project folder and run `python3 launch.py` (`py launch.py` on Windows). The first launch installs dependencies into the local `.venv`; system Python is not modified.

**Keep the phone unlocked until completion.** Screen-off iCloud Backup and ongoing Books downloads can interfere with an operation. Do not run several copies at once. If the phone is not detected, unlock it, allow wired accessories in Settings → Privacy & Security, and reconnect. On Linux, restarting `usbmuxd` may help: `sudo systemctl restart usbmuxd`.

If macOS blocks the launcher as an unsigned download, run `bash "Launch macOS.command"` from a normal terminal. Do not bypass a specific malware warning.

## Essential menu choices

| Item | Action |
| --- | --- |
| **1** | Install the normal profile using `bundle.yaml` |
| **2** | Show SIMs and an installation plan without writing |
| **3** | Check computer requirements/files; no phone needed |
| **4** | Remove IMSI aliases for one or all SIMs; **not Wholesale removal** |
| **5** | Recover interrupted work; **not uninstall** |
| **6** | Show command-line help |
| **7** | Choose another system bundle and SIM, then confirm |
| **8 / 9** | Read-only connection diagnostics / observe a test call |
| **10** | Switch normal operations between USB and experimental Wi-Fi |
| **11** | Generate a read-only profile report with your manual checks |
| **12 / 13** | Expert GID1 override / journal-bound undo; USB-only |
| **14 / 15** | Automatic T-Mobile Wholesale installation / owned removal; USB-only |

For **12–15**, type `y` or `yes` to confirm a write; Enter cancels. Item **7** accepts a blank confirmation as yes. In **14/15**, Enter at the earlier **slot prompt** requests automatic selection, not confirmation. Command-line write actions are **noninteractive**: they do not ask for menu confirmation.

## Normal bundle selection

Menu **1** uses `bundle.yaml` beside the script:

```yaml
default: Vodafone_hu
25001: Vodafone_ro
```

An exact MCCMNC entry (without spaces) wins over `default`; `25001` is MTS Russia. The default applies only to Russian (MCC 250) and Belarusian (MCC 257) SIMs. Without the file, `Vodafone_hu` is used. Other SIMs remain untouched unless their MCCMNC is configured or their slot is explicitly selected in item **7** or with `--sims 1` / `--sims 2`.

Use the case-sensitive system bundle folder name without `.bundle`. Item **7** or `--bundle` overrides the file for that operation. Compare bundle properties at [ios-bundles.github.io](https://ios-bundles.github.io); properties do not prove live service availability.

```sh
python3 launch.py --status
python3 launch.py --bundle Vodafone_ro --sims 1
python3 launch.py --restore --sims 1
```

The second command installs; the third removes that SIM's IMSI alias only. After normal installation, a reboot is recommended. Check calls and data, then enable Wi-Fi Calling under Settings → Cellular → the relevant SIM. For a Wi-Fi Calling check, enable Airplane Mode, turn Wi-Fi on and make an actual call if your carrier allows it.

## Automatic T-Mobile Wholesale

Use USB and choose **14**. Automatic selection works when there is one eligible line. With multiple eligible T-Mobile lines, explicitly choose **1** or **2**. Inspect the preview, then confirm. The installation is qualified to the selected SIM; the other active slot must retain its verified carrier/operator baseline. Unsupported or ambiguous selections stop without writing.

```sh
# Read-only preview
python3 launch.py --tmobile-wholesale --status
# Install when selection is unambiguous
python3 launch.py --tmobile-wholesale
# Explicitly preview/install SIM 1 (use 2 for SIM 2)
python3 launch.py --tmobile-wholesale --sims 1 --status
python3 launch.py --tmobile-wholesale --sims 1
# Preview/remove an owned installation; add --sims 1 or 2 if needed
python3 launch.py --remove-tmobile-wholesale --status
python3 launch.py --remove-tmobile-wholesale
```

Use `py` instead of `python3` on Windows. Commands without `--status` above are noninteractive write actions. A successful installation stays applied without a connected computer or background program. Repeating a healthy owned installation leaves it unchanged. Use **15** or `--remove-tmobile-wholesale` for explicit removal; stock or unowned Wholesale selections are not automatically removed.

## Read-only checks and recovery

```sh
python3 launch.py --check
python3 launch.py --status
python3 launch.py --diagnose
python3 launch.py --watch-call
python3 launch.py --help
# Repairs interrupted work; does not uninstall a healthy installation
python3 launch.py --recover
```

If an operation fails, preserve the project and `runs` folder, reconnect by USB, unlock the phone and use **5** or `--recover`. Do not blindly reinstall an interrupted GID1/Wholesale operation: follow its saved recovery/undo instructions. Item **4** removes IMSI aliases only; it does not remove GID1 or Wholesale rules.

Normal IMSI operations can optionally use experimental Wi-Fi after USB Trust and Finder/iTunes Wi-Fi sync setup. Keep both devices on the same non-guest network. On macOS, Terminal needs Local Network permission. If Wi-Fi fails, return to USB and recover before another write. GID1 and Wholesale installation/removal remain USB-only.

**Keep backups private.** `runs` contains catalog backups, device/SIM identifiers and ownership journals, including full ICCIDs in qualified rules. Do not publish it or assume masked console output makes archives safe. Preserve active Wholesale/GID1 owner journals and referenced backups when moving or upgrading the project; use `--runs PATH` if stored elsewhere. Removal depends on those records.

## Compatibility and service safety

The project targets iPhone 12–18 with iOS 16 or later, but compatibility depends on the device, build and computer. Cellular iPads require an active SIM/eSIM; Wi-Fi-only iPads cannot install profiles.

The write mechanism is known to be closed starting with **iOS 27.2 beta 3 (24B5099f)**. Existing rules may remain while installation, change or removal becomes unavailable. Future iOS/carrier-settings updates, SIM replacement and resets can change behavior. Remove a rule before updating if you need assured access to the current removal mechanism.

Bundle selection does not establish calls, data, VoWiFi, VoLTE, EVS, 5G bands or reboot persistence. Results depend on carrier provisioning, plan, coverage, model and iOS. Changing bundles does not bypass signed regional radio restrictions and can alter APN/MMS settings, labels or iMessage/FaceTime activation behavior. Test the services you rely on and check APN/MMS settings against your carrier's guidance if necessary.

## License and credits

CarrierSIM's own code is [MIT licensed](LICENSE). Copyright © 2026 Vladimir B / vlw and contributors. Preserve the copyright notice and license text with copies or substantial portions of the code. Original author: Vladimir B / vlw ([contact](mailto:vlwwwwww@gmail.com)).

Third-party code retains its licenses. CarrierSIM's license does not cover Apple files, including IPCC triggers in `assets.zip`, system carrier bundles or Apple libraries; these belong to their respective rights holders.

- Transfer protocol: [AirLift](https://github.com/0xjohnnydev/airlift), MIT, [LICENSE-AirLift.txt](LICENSE-AirLift.txt).
- Windows Grappa exchange: [AirCard-Windows](https://github.com/dhava-gautama/AirCard-Windows/blob/e0eadb0c88da55516f1c9f939fd4ce27560910d5/src/airtraffic.rs), MIT, [LICENSE-AirCard.txt](LICENSE-AirCard.txt); prepared Grappa data originated in [yinyajiang/go-tunes](https://github.com/yinyajiang/go-tunes).
- Linux AirTraffic backend: [AirCard-Linux](https://github.com/shinkuan/AirCard-Linux) and [its variant](https://github.com/hoicau/AirCard-Linux).
