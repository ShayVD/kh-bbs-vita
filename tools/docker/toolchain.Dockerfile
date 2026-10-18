# Linux environment for the PSP compilers that only exist as Linux i386
# binaries (Sony SDK psp-gcc 3.3.3), plus MIPS binutils for objdump.
# The compilers themselves are not in the image: they are fetched into
# tools/toolchain/ (gitignored) and the repo is mounted at /work.
#   docker build --platform linux/amd64 -t khbbs-toolchain -f tools/docker/toolchain.Dockerfile tools/docker
#   tools/tc <command>     (runs <command> in the container, cwd = repo)
FROM --platform=linux/amd64 debian:bookworm-slim
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      libc6:i386 libstdc++6:i386 binutils-mips-linux-gnu python3 make ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
