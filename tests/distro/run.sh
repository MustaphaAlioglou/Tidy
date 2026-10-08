#!/usr/bin/env bash
# usage: tests/distro/run.sh [image ...]   e.g. tests/distro/run.sh fedora:latest
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
DEFAULT=(ubuntu:24.04 ubuntu:26.04 debian:trixie fedora:latest archlinux:latest opensuse/tumbleweed)
IMAGES=("${@:-${DEFAULT[@]}}")
LOGS="${TMPDIR:-/tmp}/tidy-distro-logs"
mkdir -p "$LOGS"
status=0
summary=()
for image in "${IMAGES[@]}"; do
    tag="tidy-test:$(echo "$image" | tr ':/' '--')"
    log="$LOGS/$(echo "$image" | tr ':/' '--').log"
    echo "### $image (log: $log)"
    if ! docker build -t "$tag" --build-arg "BASE=$image" "$HERE" >"$log" 2>&1; then
        summary+=("$(printf '%-22s BUILD FAILED' "$image")"); status=1; tail -5 "$log"; continue
    fi
    if docker run --rm -v "$ROOT:/src:ro" "$tag" sh /src/tests/distro/inside.sh >>"$log" 2>&1; then
        verdict=PASS
    else
        verdict=FAIL; status=1
    fi
    result=$(grep '^RESULT' "$log" | tail -1 | cut -d' ' -f2-)
    grep -E '^== |unavailable|FAIL|Error' "$log" | head -8
    summary+=("$(printf '%-22s %-4s %s' "$image" "$verdict" "$result")")
done
echo
printf '%s\n' "${summary[@]}"
exit $status
