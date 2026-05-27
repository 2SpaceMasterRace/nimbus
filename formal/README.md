# Nimbus Formal Specs

Nimbus keeps lightweight formal artifacts next to the executable runtime spec.
These files are intentionally small and reviewable: they model the status
domains and transition kernel that protect action execution, approvals,
generation commits, and stack application.

The Python CI check verifies that these formal files stay in lock-step with
`nimbus_runtime.replay.runtime_status_spec()`. When TLC/Lean are installed, the
same files can be checked directly:

```shell
java -jar ~/.local/share/nimbus-formal/tla2tools.jar \
  -config formal/tla/NimbusActionLedger.cfg \
  formal/tla/NimbusActionLedger.tla
lean formal/lean/Nimbus/ActionLedger.lean
```

Showable demo:

```shell
uv run nimbus spec check --json
```

The command prints the executable status spec digest and the formal artifact
digests. If a developer adds a runtime status without updating the TLA+/Lean
models, the test suite fails before the mismatch can ship.
