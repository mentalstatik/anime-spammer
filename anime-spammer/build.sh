#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python prepare_bundle_images.py
.venv/bin/python -m PyInstaller \
    --clean \
    --noconfirm \
    --onefile \
    --strip \
    --name Aneemay-Linux \
    --hidden-import PIL._tkinter_finder \
    --add-data ".bundle-images:images" \
    --add-data "media:media" \
    main.py

printf '\nBuilt Linux standalone: %s/dist/Aneemay-Linux\n' "$(pwd)"
