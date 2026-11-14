# The cloud service (cloud/) as a container image: a static binary on a distroless base, with no shell or package
# manager, run as a user that isn't root. Built from the repository's root, which .dockerignore narrows to the
# files it needs:
#
#   podman build -f cloud/housecloud.Containerfile -t housecloud .
#   podman run --rm -e DATABASE_URL=postgres://… -p 8080:8080 housecloud serve -default-house example
#
# The base images are pinned by the digest of their multi-platform index, so a build is the same until the pin is
# moved on purpose, on any platform.

# Built on the builder's own platform and cross-compiled to the image's, so an arm64 image takes no emulation.
FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.27.4-alpine@sha256:0a5f7fe796599ff487605987432e7619ae45beeb2fba4c2ce0b907879932aebf AS build
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY web.go ./
COPY housemon/web/ housemon/web/
COPY cloud/ cloud/
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags="-s -w" -o /out/housecloud ./cloud/cmd/housecloud

# Only the binary goes on: distroless's static base is CA certificates, time zones and a passwd file, 3 MB.
FROM gcr.io/distroless/static-debian13:nonroot@sha256:aafe993665bf6200db38a02c11a9351e782a84b6a45e7f33b6695638c88e3931
COPY --from=build /out/housecloud /usr/local/bin/housecloud
# distroless's nonroot, by number, so Kubernetes can check runAsNonRoot without a passwd file to look it up in.
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/housecloud"]
CMD ["serve"]
LABEL org.opencontainers.image.source="https://github.com/jason-i-magno/house-monitor" \
      org.opencontainers.image.description="house-monitor's cloud service: houses' readings in, each house's page out" \
      org.opencontainers.image.licenses="MIT"
