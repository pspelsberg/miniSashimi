FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d
RUN apk add --no-cache python3 py3-pip bash
RUN addgroup -g 10002 sandboxgroup && \
    adduser -u 10002 -G sandboxgroup -D -s /bin/sh sandboxuser
WORKDIR /workspace
USER sandboxuser
VOLUME /workspace
