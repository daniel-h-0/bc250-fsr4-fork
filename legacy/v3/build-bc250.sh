#!/bin/bash
set -e
WS="${WORKSPACE:-/workspace}"
BD="${BUILD_DIR:-/build}"
VARIANT="${VARIANT:-patch}"

if [ "$VARIANT" = "patch" ]; then
    OUT="$BD/libvulkan_radeon.so"
else
    OUT="$BD/stock/libvulkan_radeon-stock.so"
    BD="$BD/stock"
fi

cd /opt/mesa
if [ "$VARIANT" = "patch" ]; then
    # V3 is the exact runtime-tested EXP-042B delta against Mesa 26.2.0.
    # It contains the BC-250 base compatibility changes, ACO i24 MAD support,
    # wrapper fusion, dense/two-chain lowering and deferred-SDot optimization.
    git apply "$WS/bc250-fsr4-v3.patch"
fi

MESON_ARGS=(
    -Dbuildtype=release
    -Dwrap_mode=nodownload
    -Dvulkan-drivers=amd
    -Dgallium-drivers=radeonsi
    -Dllvm=enabled
)

if [ -f "$BD/meson-private/coredata.dat" ]; then
    meson setup --reconfigure "$BD" "$PWD" "${MESON_ARGS[@]}"
else
    meson setup "$BD" "$PWD" "${MESON_ARGS[@]}"
fi

meson compile -C "$BD"
cp "$BD/src/amd/vulkan/libvulkan_radeon.so" "$OUT"
echo "Built [$VARIANT]: $OUT"
