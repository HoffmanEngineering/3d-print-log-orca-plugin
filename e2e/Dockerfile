# syntax=docker/dockerfile:1
# Headless OrcaSlicer 2.5 test bench: Xvfb + Mesa software GL + openbox, driven with xdotool.
# The slicer only needs to run here, not render fast, so no GPU is involved.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        ca-certificates curl jq python3 \
        xvfb openbox xdotool x11-utils imagemagick tesseract-ocr\
        mesa-utils libgl1-mesa-dri libegl1 libglu1-mesa \
        libgtk-3-0 libwebkit2gtk-4.1-0 libsoup-3.0-0 \
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libgstreamer-gl1.0-0 \
        fonts-dejavu-core locales \
    && rm -rf /var/lib/apt/lists/* \
    && localedef -i en_US -f UTF-8 en_US.UTF-8

# The AppImage is extracted at build time (no FUSE in the container). It is bind-mounted from the
# build context rather than copied, so the 200 MB file never becomes an image layer.
ARG APPIMAGE=OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-alpha.AppImage
RUN --mount=type=bind,source=cache,target=/cache \
    mkdir -p /opt && cd /opt && cp "/cache/$APPIMAGE" /tmp/orca.AppImage && chmod +x /tmp/orca.AppImage \
    && /tmp/orca.AppImage --appimage-extract >/dev/null && mv squashfs-root orca && rm /tmp/orca.AppImage

ENV DISPLAY=:99 \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    GALLIUM_DRIVER=llvmpipe \
    WEBKIT_DISABLE_COMPOSITING_MODE=1 \
    HOME=/home/tester \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

COPY scripts/ /e2e/
RUN chmod +x /e2e/*.sh /e2e/*.py && mkdir -p /in /out /data
ENTRYPOINT ["/e2e/e2e.sh"]
