#!/usr/bin/env bash
# Download this file first, inspect it, then run with your project folder.
set -euo pipefail
vault=''
clients='codex,claude-code'
port=8765
platform=macos
prepare=()
extra=()
repo="${ATLASBRAIN_INSTALL_DIR:-$HOME/.local/share/atlasbrain/app}"
source_url='https://github.com/danilofacco/atlasbrain.git'
while (($#)); do
    case "$1" in
        --vault) vault="$2"; shift 2 ;;
        --clients) clients="$2"; shift 2 ;;
        --port) port="$2"; shift 2 ;;
        --platform) platform="$2"; shift 2 ;;
        --prepare-only) prepare=(--prepare-only); shift ;;
        --desktop-config|--bridge-command-json|--client-home) extra+=("$1" "$2"); shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ -n "$vault" && -d "$vault" ]] || { echo 'Use --vault with an existing project folder.' >&2; exit 2; }
if [[ "$platform" == macos ]]; then
    [[ $(uname -s) == Darwin ]] || { echo 'This installer requires macOS; Windows uses install.ps1.' >&2; exit 2; }
    if ! /usr/bin/git --version >/dev/null 2>&1; then
        xcode-select --install || true
        echo 'Finish the Apple command line tools installation, then run this installer again.' >&2
        exit 1
    fi
fi
command -v git >/dev/null || { echo 'Git is required inside WSL (sudo apt-get install git).' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl is required.' >&2; exit 1; }
uv_bin=$(command -v uv || true)
if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then uv_bin="$HOME/.local/bin/uv"; fi
if [[ -z "$uv_bin" ]]; then
    temporary=$(mktemp)
    trap 'rm -f "$temporary"' EXIT
    curl --fail --location --proto '=https' --tlsv1.2 https://astral.sh/uv/install.sh -o "$temporary"
    sh "$temporary"
    uv_bin="$HOME/.local/bin/uv"
fi
if [[ ! -e "$repo" ]]; then
    mkdir -p "$(dirname "$repo")"
    git clone "$source_url" "$repo"
else
    [[ -d "$repo/.git" && -f "$repo/atlasbrain/desktop_install.py" ]] || {
        echo "Installation folder exists and is not a compatible AtlasBrain checkout: $repo" >&2; exit 1;
    }
    # Reuse it without discarding local work or changing branches.
fi
cd "$repo"
"$uv_bin" sync --locked --python 3.12
exec "$repo/.venv/bin/python" -m atlasbrain.desktop_install --vault "$vault" --clients "$clients" --port "$port" --platform "$platform" ${prepare[@]+"${prepare[@]}"} ${extra[@]+"${extra[@]}"}
