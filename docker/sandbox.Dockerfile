FROM alpine:3.19
RUN apk add --no-cache python3 py3-pip bash
RUN addgroup -g 10002 sandboxgroup && \
    adduser -u 10002 -G sandboxgroup -D -s /bin/sh sandboxuser
WORKDIR /workspace
USER sandboxuser
VOLUME /workspace
