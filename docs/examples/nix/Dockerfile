FROM nixos/nix:latest AS builder

COPY . /tmp/build
WORKDIR /tmp/build/docs/examples/nix

RUN nix \
    --extra-experimental-features "nix-command flakes" \
    build

RUN mkdir /tmp/nix-store-closure
RUN cp -R $(nix-store -qR result/) /tmp/nix-store-closure

FROM scratch

WORKDIR /app
COPY --from=builder /tmp/nix-store-closure /nix/store
COPY --from=builder /tmp/build/docs/examples/nix/result /app

CMD ["/app/bin/show-tool-versions"]
