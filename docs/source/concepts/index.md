# Concepts and Reliability

This section is the field guide for Nimbus terminology, built from root
`SYSTEM_DESIGN.md` and cross-checked against the current package docs plus the
{doc}`../complete-system-design` Sphinx companion. Nimbus is trying to become a
compact, event-backed AI runtime with durable actions, verification artifacts,
and deterministic reliability tests.

It is meant to be browsed like a reference, not read front to back. Start with
the glossary when a term is unfamiliar. Use the reliability and testing pages
when you are designing a runtime change, writing a test plan, or reviewing a
system-design proposal.

<div class="nimbus-resource-grid">
  <a class="nimbus-resource-card" href="system-design-map.html">
    <span>Map</span>
    <strong>System design map</strong>
    <em>How the canonical design connects the storage axis, AI runtime axis, action kernel, and reliability model.</em>
  </a>
  <a class="nimbus-resource-card" href="platform-glossary.html">
    <span>Glossary</span>
    <strong>Nimbus platform glossary</strong>
    <em>Core terms for sessions, operations, events, actions, artifacts, policy, and scale.</em>
  </a>
  <a class="nimbus-resource-card" href="reliability-properties.html">
    <span>Catalog</span>
    <strong>Reliability property catalog</strong>
    <em>Safety and liveness properties extracted from the canonical design invariants and fault plans.</em>
  </a>
  <a class="nimbus-resource-card" href="testing-techniques.html">
    <span>Testing</span>
    <strong>Testing techniques</strong>
    <em>How pytest, integration tests, property tests, fuzzing, e2e flows, and evals fit together here.</em>
  </a>
  <a class="nimbus-resource-card" href="deterministic-simulation-testing.html">
    <span>DST</span>
    <strong>Deterministic simulation testing</strong>
    <em>The long-term test harness for session/action correctness under hostile scheduling.</em>
  </a>
</div>

## Primary source corpus

These pages are intentionally centered on:

- root `SYSTEM_DESIGN.md`
- {doc}`../complete-system-design`
- {doc}`../architecture-overview`
- {doc}`../DESIGN`
- {doc}`../cloud-storage/index`
- {doc}`../ai-client-overview`
- {doc}`../ai-client-guardrails`
- {doc}`../testing`
- {doc}`../testing-playbook`

```{toctree}
:maxdepth: 2

system-design-map
platform-glossary
reliability-properties
testing-techniques
deterministic-simulation-testing
```
