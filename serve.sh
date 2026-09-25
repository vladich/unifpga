#!/usr/bin/env bash
# The board editor as a local web page: http://127.0.0.1:8765/
#
#   ./serve.sh               the editor on port 8765
#   ./serve.sh --port 9000   another port
#
# The page is the editor of tools/studio (index.html, studio.js, studio.css),
# served by tools/studio.py through `./unifpga serve`; it answers only the
# local machine. It needs Python 3 with PyYAML (requirements.txt): the python3
# on the PATH when it has PyYAML, else a virtual environment made here in
# .venv on the first run by the first interpreter that can make one with pip
# (python3, python3.13 .. 3.9, macOS's /usr/bin/python3). UNIFPGA_PYTHON names
# an interpreter to use instead of all that.

set -euo pipefail
cd "$(dirname "$0")"
prev=""

has_yaml () { "$1" -c "import yaml" > /dev/null 2>&1; }

python=""
if [ -n "${UNIFPGA_PYTHON:-}" ]; then
    python="$UNIFPGA_PYTHON"
elif [ -f .venv/.unifpga-ready ] && has_yaml .venv/bin/python; then
    python=.venv/bin/python
elif command -v python3 > /dev/null 2>&1 && has_yaml python3; then
    python=python3
else
    rm -rf .venv
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 /usr/bin/python3; do
        command -v "$candidate" > /dev/null 2>&1 || continue
        echo "serve.sh: making .venv with $candidate ($("$candidate" --version 2>&1)) and PyYAML" >&2
        if "$candidate" -m venv .venv > /dev/null 2>&1 \
                && .venv/bin/python -m pip install --quiet -r requirements.txt > /dev/null 2>&1 \
                && has_yaml .venv/bin/python; then
            touch .venv/.unifpga-ready
            python=.venv/bin/python
            break
        fi
        echo "serve.sh: $candidate could not make a virtual environment with pip; trying the next" >&2
        rm -rf .venv
    done
    if [ -z "$python" ]; then
        echo "serve.sh: no Python 3 with PyYAML, and none could make a virtual environment with pip." >&2
        echo "          Install PyYAML for a Python of yours (python3 -m pip install pyyaml) or set UNIFPGA_PYTHON=<interpreter>." >&2
        exit 1
    fi
fi

# the port: 8765 unless --port says otherwise; when 8765 is taken (an editor
# already running?) the next free one, and say so
port=""
for arg in "$@"; do
    case "$prev" in --port) port="$arg" ;; esac
    prev="$arg"
done
if [ -z "$port" ]; then
    port=$("$python" - <<'EOF'
import socket
for port in range(8765, 8790):
    try:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as the server binds
            s.bind(("127.0.0.1", port))
        print(port)
        break
    except OSError:
        pass
EOF
    )
    [ -n "$port" ] || { echo "serve.sh: no free port between 8765 and 8789" >&2; exit 1; }
    if [ "$port" != 8765 ]; then
        echo "serve.sh: port 8765 is in use (an editor already running? http://127.0.0.1:8765/) — using $port" >&2
    fi
    set -- --port "$port" "$@"
fi

exec "$python" ./unifpga serve "$@"
