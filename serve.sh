#!/usr/bin/env bash
# The board editor as a local web page: http://127.0.0.1:8765/
#
#   ./serve.sh               the editor on port 8765
#   ./serve.sh --port 9000   another port
#
# The page is the editor of tools/studio (index.html, studio.js, studio.css),
# served by tools/studio.py through `./unifpga serve`; it answers only the
# local machine. It needs Python 3 with the packages of requirements.txt
# (PyYAML, jsonschema): the .venv made here on an earlier run, else the
# python3 on the PATH when it has them all, else a virtual environment made
# in .venv by the first interpreter that can make one with pip (python3,
# python3.13 .. 3.9, macOS's /usr/bin/python3). Every run checks that the
# interpreter has every package and installs the missing ones into .venv
# (never into a Python of yours). UNIFPGA_PYTHON names an interpreter to use
# instead of all that; it must have the packages.

set -euo pipefail
cd "$(dirname "$0")"
prev=""

# the packages of requirements.txt, by the name they are imported under
MODULES="yaml jsonschema"

missing_deps () {                       # the modules "$1" cannot import, space separated
    local m out=""
    for m in $MODULES; do
        "$1" -c "import $m" > /dev/null 2>&1 || out="$out $m"
    done
    echo "${out# }"
}

has_deps () { [ -z "$(missing_deps "$1")" ]; }

install_deps () {                       # requirements.txt into "$1"; true when every module imports afterwards
    "$1" -m pip install --quiet -r requirements.txt > /dev/null 2>&1 && has_deps "$1"
}

make_venv () {                          # a fresh .venv with the requirements; sets $python
    rm -rf .venv
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 /usr/bin/python3; do
        command -v "$candidate" > /dev/null 2>&1 || continue
        echo "serve.sh: making .venv with $candidate ($("$candidate" --version 2>&1)) and requirements.txt" >&2
        if "$candidate" -m venv .venv > /dev/null 2>&1 && install_deps .venv/bin/python; then
            touch .venv/.unifpga-ready
            python=.venv/bin/python
            return 0
        fi
        echo "serve.sh: $candidate could not make a virtual environment with the requirements; trying the next" >&2
        rm -rf .venv
    done
    echo "serve.sh: no Python 3 with the requirements ($MODULES), and none could make a virtual environment with pip." >&2
    echo "          Install them for a Python of yours (python3 -m pip install -r requirements.txt) or set UNIFPGA_PYTHON=<interpreter>." >&2
    exit 1
}

python=""
if [ -n "${UNIFPGA_PYTHON:-}" ]; then
    python="$UNIFPGA_PYTHON"
    missing=$(missing_deps "$python")
    if [ -n "$missing" ]; then
        echo "serve.sh: UNIFPGA_PYTHON=$python lacks $missing; install the requirements for it:" >&2
        echo "          $python -m pip install -r requirements.txt" >&2
        exit 1
    fi
elif [ -x .venv/bin/python ]; then
    python=.venv/bin/python
    missing=$(missing_deps "$python")
    if [ -n "$missing" ]; then
        echo "serve.sh: .venv lacks $missing; installing requirements.txt into it" >&2
        if ! install_deps "$python"; then
            if [ -f .venv/.unifpga-ready ]; then       # a .venv this script made: make it again
                echo "serve.sh: the install failed; making .venv again" >&2
                make_venv
            else
                echo "serve.sh: the install into .venv failed and .venv is not one this script made; fix it or remove it." >&2
                exit 1
            fi
        fi
    fi
elif command -v python3 > /dev/null 2>&1 && has_deps python3; then
    python=python3
else
    make_venv
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
