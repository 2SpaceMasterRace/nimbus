# Concepts and Reliability

This section is the field guide for Nimbus terminology, built primarily from
the three `agent-platform-design*.md` documents. Those files define the
platform Nimbus is trying to become: a multiplayer, agent-first session engine
with durable events, authorized actions, verification artifacts, and
deterministic reliability tests.

It is meant to be browsed like a reference, not read front to back. Start with
the glossary when a term is unfamiliar. Use the reliability and testing pages
when you are designing a runtime change, writing a test plan, or reviewing an
agent-platform proposal.

<div class="nimbus-resource-grid">
  <a class="nimbus-resource-card" href="agent-platform-design-map.html">
    <span>Map</span>
    <strong>Agent platform design map</strong>
    <em>How the 1.0, 2.0, and 3.0 design passes fit together and which concepts each one contributes.</em>
  </a>
  <a class="nimbus-resource-card" href="platform-glossary.html">
    <span>Glossary</span>
    <strong>Nimbus platform glossary</strong>
    <em>Core terms from the design trilogy: sessions, operations, events, actions, artifacts, policy, and scale.</em>
  </a>
  <a class="nimbus-resource-card" href="reliability-properties.html">
    <span>Catalog</span>
    <strong>Reliability property catalog</strong>
    <em>Safety and liveness properties extracted from the design docs' invariants and fault plans.</em>
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

- {doc}`../nimbus/agent-platform-design-3`
- {doc}`../nimbus/agent-platform-design-2`
- {doc}`../nimbus/agent-platform-design`

The current package and product docs are secondary cross-checks for what exists
today:

- {doc}`../architecture-overview`
- {doc}`../DESIGN`
- {doc}`../cloud-storage/index`
- {doc}`../ai-client-overview`
- {doc}`../ai-client-guardrails`
- {doc}`../testing`
- {doc}`../testing-playbook`

```{toctree}
:maxdepth: 2

agent-platform-design-map
platform-glossary
reliability-properties
testing-techniques
deterministic-simulation-testing
```
