# Harden Default Runtime Surface Design

## Goal

Make the default container runtime safer without adding a separate production
mode. Development and CI surfaces remain available only through explicit
environment opt-ins.

## Scope

This change addresses GitHub issue #2. It hardens authentication defaults,
host exposure, Business Central service flags, test automation, and the Linux
authentication compatibility patch. SQL durability and database safety settings
belong to issue #3.

## Design

The default `docker compose up` path must refuse default credentials. The compose
file will require `BC_USERNAME`, `BC_PASSWORD`, and `SA_PASSWORD` through
parameter expansion, and the entrypoint will repeat the same validation for
users who run the image outside compose. The validation rejects `admin/admin`
and `Passw0rd123!`.

The default compose network surface will bind host ports to `127.0.0.1`, so
local tools can still reach the standard BC ports while they are not exposed on
external host interfaces. SQL will no longer publish a host port; it remains
reachable to the `bc` service on the compose network.

Business Central service flags become explicit environment-controlled booleans.
Client Services, SOAP, OData, and API remain enabled by default. DevServices,
legacy Management, Management API, test automation, and the test toolkit default
to disabled. Existing CI and reusable workflow paths that compile, publish, or
run tests will opt into the dev/test flags explicitly.

The `NavUser.TryAuthenticate` compatibility bypass becomes an explicit opt-in
through `BC_ALLOW_INSECURE_AUTH_BYPASS=true`. With the default `false`, the
startup hook leaves the Microsoft authentication path unchanged.

Readiness must no longer depend on DevServices. The entrypoint will wait on the
enabled Client Services endpoint for startup readiness, and it will skip all
dev-endpoint publishing unless DevServices is enabled. The healthcheck and
`verify-network-surface.sh` will probe only enabled surfaces by default, with
an option to verify the full dev/test surface when CI opts into it.

## Documentation

The README quickstart must show explicit credentials before `docker compose up`.
The endpoint table must distinguish default local-only endpoints from opt-in
dev/test endpoints. Workflow examples must stop advertising `admin/admin`.

## Tests

Static tests will verify:

- compose requires explicit credentials and does not publish SQL;
- compose host bindings default to loopback;
- entrypoint defaults disable DevServices, Management, Management API, test
  automation, and test toolkit;
- the auth bypass is gated behind `BC_ALLOW_INSECURE_AUTH_BYPASS=true`;
- CI workflows opt into the dev/test surface when they need it.

These tests avoid booting a full BC container while still covering the behavior
that controls the generated runtime configuration.
