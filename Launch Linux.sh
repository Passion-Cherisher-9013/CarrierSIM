#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PYTHONUTF8=1
carrier_python=""
for candidate in python3.12 python3.11 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
        carrier_python="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$carrier_python" ]; then
    echo 'Install Python 3.11 or newer.'
    exit 1
fi
exec "$carrier_python" -u launch.py "$@"
