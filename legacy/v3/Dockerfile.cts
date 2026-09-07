FROM --platform=linux/amd64 fedora:44

# Build deps for VK-GL-CTS (deqp-vk) + runtime deps needed to execute the
# RADV drivers (the patched/stock libvulkan_radeon*.so link against this
# image's LLVM/libdrm, matching the `bc250-fsr4:builder` image).
RUN dnf install -y --setopt=install_weak_deps=False \
    gcc gcc-c++ make cmake ninja-build pkgconf-pkg-config git \
    python3 python3-devel python3-setuptools python3-numpy python3-jinja2 \
    python3-PyYAML python3-iniparse python3-lxml \
    vulkan-loader llvm-libs libdrm elfutils-libelf zlib libzstd \
    libX11 libxcb libxshmfence libXrandr libXxf86vm wayland-libs libglvnd \
    wayland-devel wayland-protocols-devel \
  && dnf clean all

ARG CTS_COMMIT=main
RUN git clone https://github.com/KhronosGroup/VK-GL-CTS.git /opt/cts \
  && git -C /opt/cts checkout -q "$CTS_COMMIT" \
  && python3 /opt/cts/external/fetch_sources.py

RUN cmake -S /opt/cts -B /opt/cts/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DSELECTED_BUILD_TARGETS="deqp-vk" \
  && cmake --build /opt/cts/build --target deqp-vk

# Run deqp-vk from its build tree (keeps its runtime data-dir discovery valid).
CMD ["/opt/cts/build/external/vulkancts/modules/vulkan/deqp-vk"]
