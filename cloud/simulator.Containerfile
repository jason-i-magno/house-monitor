# The simulator as a container image: the Python app modelling the example house, its readings sent to the cloud
# service by the uplink, for the public demo. Built from the repository's root, which .dockerignore narrows to the
# files it needs, so the real house (private/) can't end up in it:
#
#   podman build -f cloud/simulator.Containerfile -t housemon-simulator .
#   podman run --rm -v ./uplink.json:/run/secrets/housemon/uplink.json:ro -v housemon:/var/lib/housemon housemon-simulator
#
# One stage: the app is the standard library alone, so there's nothing to build and leave behind. The base image is
# pinned by the digest of its multi-platform index.
FROM docker.io/library/python:3.14-alpine@sha256:5862d971d1f0ae26b258cefa4e893556b1d3aba81bd2ff30888704ad378bab67

# A user that isn't root, by number, so Kubernetes can check runAsNonRoot, and the one place it writes, its database,
# for a volume to go over: the rest of the file system can be read-only.
RUN addgroup -S -g 10001 housemon \
 && adduser -S -D -H -u 10001 -G housemon housemon \
 && install -d -o 10001 -g 10001 /var/lib/housemon
WORKDIR /app
COPY housemon/ housemon/
# Compiled now, as root, since the app can't write beside its code.
RUN python3 -m compileall -q housemon
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001

# The example house, always, never a private one. No sign-in files are in the image, and the app can't write any, so
# the ecobee, Whisker and MOVA sources, which sign in as their apps do unofficially, never run in the cloud; without
# bleak, neither does Govee. The uplink runs when its file is mounted, as from a Kubernetes Secret. The page it serves
# itself stays inside the container, on 127.0.0.1.
ENTRYPOINT ["python3", "-m", "housemon"]
CMD ["--house", "housemon/example_house.py", "--db", "/var/lib/housemon/simulated.sqlite3", "--uplink", "/run/secrets/housemon/uplink.json"]
LABEL org.opencontainers.image.source="https://github.com/jason-i-magno/house-monitor" \
      org.opencontainers.image.description="house-monitor's simulated example house, sending its readings to the cloud service" \
      org.opencontainers.image.licenses="MIT"
