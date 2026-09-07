# Build the actual sshx client from a pinned, locked Rust release.
# musl keeps it compatible with Ubuntu 22.04's older glibc.
FROM rust:1.85-alpine AS sshx-builder
RUN apk add --no-cache build-base musl-dev protobuf-dev protoc openssl-dev openssl-libs-static pkgconf cmake
RUN cargo install sshx --version 0.4.1 --locked --root /out

FROM ubuntu:22.04
LABEL io.cloudy.os="ubuntu:22.04" io.cloudy.image="1.0-dev"
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget git nano procps htop tmux iproute2 \
    python3 python3-pip openssh-client coreutils bash \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sshx-builder /out/bin/sshx /usr/local/bin/sshx
RUN /usr/local/bin/sshx --version \
    && printf '\nWelcome to Cloudy VPS — Ubuntu 22.04 LTS\nType exit to close this shell. Your container continues running.\n\n' > /etc/motd \
    && printf '\n[ -t 1 ] && cat /etc/motd\n' >> /root/.bashrc
ENV TERM=xterm-256color LANG=C.UTF-8
WORKDIR /root
CMD ["/bin/sleep", "infinity"]
