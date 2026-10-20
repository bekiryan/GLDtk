#!/usr/bin/env bash
# Install the haxelibs the LDtk fork builds with, at the versions GLDtk is tested with.
# deepnightLibs must stay at e1237f8 (newer commits break the build). Used by CI; on a dev
# machine run it once into a fresh haxelib repo (`haxelib setup <dir>` first).
# Works in bash on macOS and Linux.
set -euo pipefail
REPO="$(haxelib config | tr -d '\r')"
REPO="${REPO%/}"

haxelib install format 3.8.0 --always --quiet
haxelib install ase 2.1.1 --always --quiet
haxelib install uuid 2.4.1 --always --quiet

pin() {  # name url commit
  local dir="$REPO/$1-pinned"
  if [ ! -d "$dir/.git" ]; then
    git init -q "$dir"
    git -C "$dir" remote add origin "$2"
  fi
  git -C "$dir" fetch -q --depth 1 origin "$3"
  git -C "$dir" checkout -q --force FETCH_HEAD
  haxelib dev "$1" "$dir" --quiet
}
pin castle         https://github.com/deepnight/castle                  261c8db1f47279f50444737cff368486244bdf7b
pin deepnightLibs  https://github.com/deepnight/deepnightLibs.git       e1237f80ad54f8b0a42cc3578817290dd5944454
pin electron       https://github.com/deepnight/hxelectron.git         e39a3623fa68382139a53cbf347d657d51c31b67
pin heaps          https://github.com/deepnight/heaps.git              e5a3d382ac8965cd8ffc7ae3bbca0ac3ec1a563c
pin heaps-aseprite https://github.com/AustinEast/heaps-aseprite.git    09a0b4fc7257741b16e97dddde7d2f41509383e0
pin hxnodejs       https://github.com/HaxeFoundation/hxnodejs.git      ba7d9a566a79b3b36b4e1f58bc8e66306983e9e4
pin ldtk-haxe-api  https://github.com/deepnight/ldtk-haxe-api.git      22d7ee209b7a9f5775d53407253ec9432fc588db
haxelib list
