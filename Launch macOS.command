#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUTF8=1
carrier_python=""
for candidate in python3.12 python3.11 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
        carrier_python="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$carrier_python" ]; then
    echo 'Install Python 3.11 or newer: https://www.python.org/downloads/macos/'
    carrier_status=1
else
    "$carrier_python" -u launch.py "$@"
    carrier_status=$?
fi
if [ "$carrier_status" -ne 0 ] && [ "$#" -eq 0 ] && [ -t 0 ]; then
    read -r -p 'Press Enter to close this window…' carrier_reply
fi
exit "$carrier_status"
