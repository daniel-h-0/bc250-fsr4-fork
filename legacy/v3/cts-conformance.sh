#!/bin/bash
# Differential Vulkan CTS conformance harness.
#
# Runs the Khronos Vulkan CTS (deqp-vk) twice on the BC-250 host - once under
# the patched FSR4 RADV driver and once under the unpatched same-commit
# driver - then diffs per-case verdicts to prove the patch broke nothing.
#
# REQUIRES the BC-250 AMD GPU host (this repo builds but cannot execute CTS:
# there is no AMD GPU in a QEMU/Docker harness, rendering needs the real
# device). The host needs: Docker, the `amdgpu` kernel module, and a
# /dev/dri/renderD* node. The box is a gaming machine running a Wayland
# compositor, so the focused caselist also exercises dEQP-VK.wsi.wayland.*.
#
# Usage: ./cts-conformance.sh [--focused|--full]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESA_IMG="bc250-fsr4:builder"
CTS_IMG="bc250-fsr4:cts"
DEQP="/opt/cts/build/external/vulkancts/modules/vulkan/deqp-vk"

MODE="focused"
[ "${1:-}" = "--full" ] && MODE="full"
PLATFORM=""
if [ "$(uname -m)" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then
    echo "NOTE: emulated amd64 build under QEMU; must STILL run on a host with the BC-250 GPU."
    PLATFORM="--platform linux/amd64"
fi

[ -e /dev/dri ] || {
    echo "ERROR: /dev/dri not present - CTS needs the real AMD GPU (BC-250 host only)."
    exit 1
}

# Wayland display passthrough for the dEQP-VK.wsi.wayland.* cases. Only wired
# up when a Wayland display is actually available, so headless runs still work.
WAYLAND_OPTS=()
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
    WAYLAND_SOCK="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
    WAYLAND_OPTS=(--env XDG_RUNTIME_DIR --env WAYLAND_DISPLAY \
        --volume "$WAYLAND_SOCK:$WAYLAND_SOCK")
fi

COMMIT="$(head -n1 "$DIR/mesa-commit.txt")"

# 1. Builder image (pristine Mesa at the pinned commit).
# Always rebuilt so a change to mesa-commit.txt (or to the patch/Mesa source)
# is picked up; matches build-anywhere.sh semantics.
docker info >/dev/null 2>&1 || { echo "Docker not running."; exit 1; }
echo "Building $MESA_IMG (Mesa $COMMIT)..."
docker buildx build --tag "$MESA_IMG" --load $PLATFORM \
    --build-arg MESA_COMMIT="$COMMIT" "$DIR"

# 2. Build both driver variants (patch and stock), then copy the host-owned
#    .so files into the repo root (build-bc250.sh writes to .build so the
#    container doesn't leave root-owned files in the workspace).
mkdir -p "$DIR/.build"
for v in patch stock; do
    echo "=== building $v driver variant ==="
    docker run --rm $PLATFORM \
        -e VARIANT="$v" \
        -v "$DIR:/workspace" \
        -v "$DIR/.build:/build" \
        "$MESA_IMG"
done
cp "$DIR/.build/libvulkan_radeon.so" "$DIR/libvulkan_radeon.so"
cp "$DIR/.build/stock/libvulkan_radeon-stock.so" "$DIR/libvulkan_radeon-stock.so"

# 3. CTS image (VK-GL-CTS, deqp-vk).
echo "Building $CTS_IMG ..."
docker buildx build --tag "$CTS_IMG" --load $PLATFORM \
    -f "$DIR/Dockerfile.cts" "$DIR"

# 4. Ephemeral ICD JSONs referencing the in-container driver paths.
mkdir -p "$DIR/.cts-icd" "$DIR/.cts-out"
cat > "$DIR/.cts-icd/radv-patched.json" <<EOF
{"file_format_version":"1.0.0","ICD":{"library_path":"/ws/libvulkan_radeon.so","api_version":"1.4.0"}}
EOF
cat > "$DIR/.cts-icd/radv-stock.json" <<EOF
{"file_format_version":"1.0.0","ICD":{"library_path":"/ws/libvulkan_radeon-stock.so","api_version":"1.4.0"}}
EOF

# 5. Case selection.
if [ "$MODE" = "full" ]; then
    CASES="dEQP-VK.*"
else
    CASES="$(grep -v '^[[:space:]]*#' "$DIR/cts/caselist-focused.txt" | grep -v '^[[:space:]]*$' | tr '\n' ',' | sed 's/,$//')"
fi
echo "Running CTS caselist in $MODE mode:"
echo "  $CASES"

run_one() { # $1 tag, $2 icd-name
    echo "=== deqp-vk under $1 driver ==="
    if docker run --rm $PLATFORM \
        --device /dev/dri --group-add video \
        -v "$DIR:/ws" \
        -v "$DIR/.cts-icd:/icd" \
        -v "$DIR/.cts-out:/out" \
        "${WAYLAND_OPTS[@]}" \
        -e VK_DRIVER_FILES="/icd/$2" \
        "$CTS_IMG" \
        "$DEQP" \
        --deqp-case="$CASES" \
        --deqp-log-filename="/out/$1.qpa" \
        --deqp-log-images=disable \
        --deqp-log-shader-sources=disable; then
        return 0
    fi
    echo "WARNING: deqp-vk under $1 driver exited nonzero (rc=$?); continuing to diff."
}

run_one patched radv-patched.json
run_one stock radv-stock.json

echo "=== comparing stock vs patched ==="
python3 "$DIR/cts/cts-diff.py" "$DIR/.cts-out/stock.qpa" "$DIR/.cts-out/patched.qpa"
rc=$?
echo "results: $DIR/.cts-out/stock.qpa , $DIR/.cts-out/patched.qpa"
exit $rc
