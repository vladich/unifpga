#!/usr/bin/env bash
# The board editor as a local web page: http://127.0.0.1:8765/
#
#   ./serve.sh               the editor on port 8765 (the next free one when 8765 is taken, and it says so)
#   ./serve.sh --port 9000   another port
#
# The page is the editor of tools/studio (index.html, studio.js, studio.css),
# served by tools/studio.py through `./unifpga serve`; it answers only the
# local machine. The ./unifpga launcher brings the Python it needs: the
# python3 on the PATH when it has the packages of requirements.txt (PyYAML,
# jsonschema), else the repository's .venv, made on first use with the
# requirements and kept complete (never a Python of yours). UNIFPGA_PYTHON
# names an interpreter to use instead; it must have the packages.

set -euo pipefail
cd "$(dirname "$0")"
exec ./unifpga serve "$@"
