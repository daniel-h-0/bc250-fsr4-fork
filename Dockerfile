FROM --platform=linux/amd64 archlinux:base-devel
RUN pacman -Syu --noconfirm --needed python python-pip git ninja \
    libdrm libelf zlib zstd libx11 libxext libxcb libxshmfence \
    libxrandr libxxf86vm wayland libdisplay-info spirv-tools glslang \
    && pacman -Scc --noconfirm
COPY requirements-build.txt /opt/requirements-build.txt
RUN python -m venv /opt/build-env \
    && /opt/build-env/bin/python -m pip install --no-cache-dir -r /opt/requirements-build.txt
ENV PATH="/opt/build-env/bin:${PATH}"
WORKDIR /workspace
ENTRYPOINT ["python", "/workspace/scripts/build.py"]
