#!/bin/sh
# Clone the Vexo Open kit next to this project. The kit is a reference: leave it unchanged.
# Your programs stay in this folder. The kit is gitignored.
set -eu
cd "$(dirname "$0")/.."
if [ -f vexo-open-kit/AGENTS.md ]; then
  echo "vexo-open-kit/ is already here."
else
  git clone --depth 1 https://github.com/VexoAI-Org/vexo-open-kit.git vexo-open-kit
fi
test -f vexo-open-kit/AGENTS.md
test -f vexo-open-kit/examples/python/speech.py
echo "Kit ready in vexo-open-kit/ (not part of this repository)."
