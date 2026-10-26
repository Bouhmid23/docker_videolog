FROM golang:1.24.8-alpine AS build

ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="${MINIO_COMMIT}" \
      org.opencontainers.image.licenses="AGPL-3.0"

RUN apk add --no-cache git

WORKDIR /src

RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin "${MINIO_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}"

RUN mkdir -p /out \
    && LDFLAGS="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go 2025-10-15T17:29:55Z)" \
    && CGO_ENABLED=0 go build -tags kqueue -trimpath \
        --ldflags "${LDFLAGS}" -o /out/minio .

FROM alpine:3.22

RUN apk add --no-cache ca-certificates curl \
    && mkdir -p /licenses

COPY --from=build /out/minio /usr/bin/minio
COPY --from=build /src/LICENSE /licenses/LICENSE

EXPOSE 9000 9001
VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
