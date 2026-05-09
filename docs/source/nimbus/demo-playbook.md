# Nimbus Demo Playbook

Nimbus is a cloud-storage agent. Version control, formal specs, plans,
approvals, and evidence are the safety machinery around that core promise.

## ELI5 Concepts

| Term | Plain English | Demo command |
|---|---|---|
| Root | A bucket/prefix Nimbus is allowed to protect. | `uv run nimbus root protect --container "$AWS_BUCKET_NAME" --prefix demo/ --name demo` |
| Generation | A snapshot of what existed under that root at one moment. | `uv run nimbus generation create <root-id>` |
| Manifest | The receipt for a generation: object keys, sizes, hashes, and digest. | `uv run nimbus manifest list` |
| Plan | A proposed change that has not run yet. | `uv run nimbus plan cleanup <manifest-id>` |
| Stack | A reviewed set of storage changes derived from a plan. | `uv run nimbus stack propose <plan-id>` |
| Restack | Re-check a stack against a fresh manifest before applying. | `uv run nimbus stack restack <stack-id> --manifest <fresh-manifest-id>` |
| Heal | Verify a root or replica and recommend or apply repair. | `uv run nimbus heal root <root-id>` |
| Evidence | Durable proof payloads that can be exported, previewed, and compacted. | `uv run nimbus evidence preview <artifact-id>` |
| Spec | Runtime status vocabulary plus formal TLA+/Lean artifacts. | `uv run nimbus spec check --json` |

## Phase-By-Phase Demo Map

Use a split screen whenever possible: Slack on the left, terminal on the right.
If a phase is CLI-only today, keep Slack open on `@Nimbus status` so the room
still sees one Nimbus workspace with multiple surfaces.

### Phase 0: Coherent Slack And CLI

```text
+ SLACK: #ops-storage ----------------++ TERMINAL -------------------------+
| @Nimbus save all files in this       || $ uv run nimbus task list --watch |
| channel to S3                        || ID      INTENT        STATUS      |
|                                      || t-9a3   save files    scanning    |
| Nimbus: saved 14 files               || t-9a3   save files    done        |
| manifest art-m01, receipt art-r01    || $ uv run nimbus proof show latest |
+--------------------------------------++-----------------------------------+
```

Say: the chat surface and terminal surface observe the same task, artifact, and
proof story. Slack is not a toy bot bolted onto a separate system.

### Phase 1: Snapshot Manifests And Proof Receipts

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus status                       || $ uv run nimbus root protect      |
| Nimbus: workspace healthy            || $ uv run nimbus generation create |
| recent artifact: art-m01             || $ uv run nimbus manifest list     |
|                                      || newest: gen-02 manifest art-m02   |
+--------------------------------------++-----------------------------------+
```

Say: a generation is a signed photograph of a bucket prefix. Repeating the
snapshot over the same objects gives the same manifest digest.

### Phase 2: Candidate Plans And Safer Cleanup

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus delete the duplicate files   || $ uv run nimbus plan cleanup art-m |
| Nimbus: approval required            || candidates:                       |
| [A] delete exact duplicates          || A delete 6, frees 142 MB          |
| [B] archive cold duplicates          || B archive 6, keeps restore path   |
| [C] tag for review                   || C tag only, no delete             |
+--------------------------------------++-----------------------------------+
```

Say: the model can propose, but it cannot silently mutate storage. The runtime
stores candidate plans and binds approval to one selected plan.

### Phase 3: Stacked Storage Diffs

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: stack awaiting approval      || $ uv run nimbus stack diff stk-1  |
| 1. copy safety backup                || 1 COPY backup/q1.pdf              |
| 2. delete duplicate q1-copy.pdf      || 2 DELETE q1-copy.pdf requires OK  |
| [Approve] [Reject]                   || apply stops at first failed proof |
+--------------------------------------++-----------------------------------+
```

Say: risky storage work becomes a stack of small reviewable changes. Protective
steps can run separately from destructive steps.

### Phase 4: Verification And Drift

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus save all files in channel    || $ aws s3 rm s3://bucket/demo/a.pdf|
| Nimbus: saved, manifest art-m01      || $ uv run nimbus verify art-m01    |
|                                      || DRIFT: a.pdf status=MISSING       |
+--------------------------------------++-----------------------------------+
```

Say: Nimbus does not just log what it intended. It checks reality later and
shows the exact object that no longer matches the receipt.

### Phase 5: Restack, Conflicts, And Operation Log

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: stack has a conflict         || $ uv run nimbus stack restack     |
| q1.pdf changed after approval        || conflict_artifact art-c01         |
| stale approval cannot apply          || $ uv run nimbus blame demo/q1.pdf |
+--------------------------------------++-----------------------------------+
```

Say: this is the version-control behavior. If storage changes underneath an
approved plan, Nimbus invalidates stale authority and asks for a new decision.

### Phase 6: Learning As Policy Patches

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: policy patch proposed        || $ uv run nimbus policy patch      |
| repeated listing under demo/reports  ||   propose --capability delete_file|
| [Accept] [Reject]                    || proposal=p-77 base_policy=v3      |
+--------------------------------------++-----------------------------------+
```

Say: learning becomes a proposed policy patch, not hidden authority. Humans
review the rule before it changes what the agent may do.

### Phase 7: S3-Only Self-Healing

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: replica lane unhealthy       || $ uv run nimbus heal replica src  |
| 3 missing approved replicas          || missing=3 repairable=3            |
| repair requires policy flag          || $ uv run nimbus heal replica ...  |
|                                      ||   --allow-missing-repair --apply  |
+--------------------------------------++-----------------------------------+
```

Say: missing replicas can be repaired only when policy allows it. Hash
mismatches stay blocked because copying over corruption would hide the problem.

### Phase 8: Migration Decision Packets

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus status                       || $ uv run nimbus migration evaluate|
| pending decision packet art-mdp01    || expected benefit, break-even,     |
|                                      || assumptions, rollback, checks     |
+--------------------------------------++-----------------------------------+
```

Say: region or replica moves are not vibes. Nimbus records the measurement,
assumptions, cost model, safety checks, and rollback plan.

### Phase 9: Replay Harness

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| incident thread: failed cleanup      || $ uv run nimbus trace export sess |
|                                      || $ uv run nimbus trace replay sess |
|                                      || STRICT DIFF: no drift             |
+--------------------------------------++-----------------------------------+
```

Say: a production bug becomes a replay file. The replay client cannot touch
real S3; it proves the runtime behavior deterministically.

### Phase 10: Formal Kernel Specs

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus status                       || $ uv run nimbus spec check --json |
|                                      || tla_digest=... lean_digest=...    |
|                                      || $ java -jar tla2tools.jar ...     |
|                                      || TLC: No error has been found      |
+--------------------------------------++-----------------------------------+
```

Say: TLA+ model-checks the finite action ledger and Lean checks terminal-state
theorems. The advantage is regression pressure: future code cannot casually
rename or violate the runtime state vocabulary.

### Phase 11: Multi-Provider Readiness

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| @Nimbus tools                        || $ uv run nimbus provider          |
| storage tools: S3 live, other        ||   capabilities --json             |
| providers future via same contract   || copy=true delete=true restore=?   |
+--------------------------------------++-----------------------------------+
```

Say: S3 remains production, but the runtime asks for capabilities instead of
importing provider SDK details into policy or actions.

### Phase 12: Provider Advisory Health

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: provider degraded            || $ uv run nimbus provider health   |
| LIST ok, HEAD slow, AWS link shown   || probe=list latency=83ms ok        |
| AWS status is advisory only          || probe=head latency=1900ms slow    |
+--------------------------------------++-----------------------------------+
```

Say: status pages enrich the message, but live tenant-scoped probes decide the
health outcome.

### Phase 13: Object-Backed Evidence Store

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: artifact art-m01 available   || $ uv run nimbus evidence export   |
| preview ready                        || payload=sha256:... gzip=sha256... |
|                                      || record uri=file://evidence/...    |
+--------------------------------------++-----------------------------------+
```

Say: artifacts are content-addressed evidence objects, not terminal scraps.
Repeated payloads dedupe bytes without merging audit records.

### Phase 14: Evidence Compaction And Preview

```text
+ SLACK -------------------------------++ TERMINAL -------------------------+
| Nimbus: compact preview for art-m01  || $ uv run nimbus evidence compact  |
| 14 objects, 2 duplicate groups       || bundle index written              |
| canonical proof remains immutable    || source evidence retained=true     |
+--------------------------------------++-----------------------------------+
```

Say: compaction makes old evidence cheap to browse, while canonical proof bytes
remain immutable and independently verifiable.

## Demo 1: Proof-Carrying Snapshot

Start with a throwaway S3 prefix containing a few files.

```shell
uv run nimbus root protect --container "$AWS_BUCKET_NAME" --prefix demo/ --name demo
uv run nimbus generation create <root-id> --json
uv run nimbus manifest list
uv run nimbus verify <manifest-id>
```

Say: Nimbus is not just listing S3. It is turning the listing into a manifest
receipt with a stable digest. That receipt can be verified later.

## Demo 2: Manual S3 Drift

Delete one object outside Nimbus, then ask Nimbus to verify the old receipt.

```shell
aws s3 rm "s3://$AWS_BUCKET_NAME/demo/q1-report.pdf"
uv run nimbus verify <manifest-id>
```

Expected result: verification exits non-zero and shows the missing object. This
is the cleanest proof-carrying moment: Nimbus can prove that the bucket reality
no longer matches the signed manifest.

## Demo 3: Cleanup Candidates

Create a manifest with duplicate object hashes, then generate cleanup plans.

```shell
uv run nimbus plan cleanup <manifest-id> --json
uv run nimbus plan list
uv run nimbus plan diff <plan-id>
uv run nimbus plan approve <plan-id>
```

Say: the model can propose, but the runtime owns the plan record and approval
state. Candidate plans are durable and reviewable before anything mutates S3.

## Demo 4: Stack, Restack, Apply

Turn an approved plan into an applyable storage stack.

```shell
uv run nimbus stack propose <plan-id> --json
uv run nimbus stack approve <stack-id>
uv run nimbus generation create <root-id> --json
uv run nimbus stack restack <stack-id> --manifest <fresh-manifest-id>
uv run nimbus stack apply <stack-id> --yes --json
```

Say: restack is the safety check that catches "the world changed while we were
reviewing." It compares the approved targets against a fresh manifest before
execution.

## Demo 5: Replica Healing

Create a source manifest and a replica manifest, then ask Nimbus what is
missing.

```shell
uv run nimbus heal replica <source-manifest-id> \
  --replica-manifest <replica-manifest-id> \
  --allow-missing-repair --json

uv run nimbus heal replica <source-manifest-id> \
  --replica-manifest <replica-manifest-id> \
  --allow-missing-repair --apply --json
```

Expected result: dry-run mode explains missing replica objects. Apply mode uses
provider-side copy when supported, verifies the destination, and writes repair
receipts.

## Demo 6: Formal Kernel Check

```shell
uv run nimbus spec check --json
java -jar ~/.local/share/nimbus-formal/tla2tools.jar \
  -config formal/tla/NimbusActionLedger.cfg \
  formal/tla/NimbusActionLedger.tla
~/.elan/bin/lean formal/lean/Nimbus/ActionLedger.lean
```

Expected result: a stable runtime status digest plus formal spec file digests
for:

- `formal/tla/NimbusActionLedger.tla`
- `formal/tla/NimbusActionLedger.cfg`
- `formal/lean/Nimbus/ActionLedger.lean`

Say: this is not model theater. The runtime emits a digest, pytest checks that
Python/TLA+/Lean agree, TLC model-checks the finite action ledger, and Lean
checks the terminal-state theorems.

## Demo 7: Provider Health Evidence

```shell
uv run nimbus provider health --prefix demo/ --json
```

Expected result: a `provider_health` artifact with bounded LIST/HEAD probe
latency, status, confidence, expiry, and advisory AWS status links. Say: AWS
status pages are context, not proof; Nimbus trusts the tenant-scoped live probe.

## Demo 8: Evidence Preview And Compaction

```shell
uv run nimbus evidence export <artifact-id> --json
uv run nimbus evidence preview <artifact-id>
uv run nimbus evidence compact <artifact-id> <artifact-id-2> --json
```

Say: artifacts are not loose terminal output. Nimbus can export payload bytes
to content-addressed evidence objects and bundle them without deleting the
original evidence.

## Slack Split-Screen Demo

Run this on the terminal side:

```shell
uv run nimbus task list --watch --profile local
```

On Slack:

```text
@Nimbus save files from #ops-storage and #contracts
```

Then reply in the same thread, without mentioning Nimbus:

```text
find duplicate files in my bucket
```

Expected result: Slack saves both channels and posts a combined report. The
follow-up reply works because the first mention activated the thread. The
dedupe card checks Nimbus-saved Slack manifest rows across the workspace.

## Slack Scheduled Drift Alert Demo

Speed up the loop for the live demo environment:

```shell
export NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS=15
export NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS=2
```

In Slack:

```text
@Nimbus save all files in this channel to S3
```

In a terminal, delete the saved prefix or one key:

```shell
aws s3 rm "s3://$AWS_BUCKET_NAME/<saved-slack-prefix>/q1-report.pdf"
```

Expected result: the scheduled verifier HEAD-checks the saved manifest rows and
posts a "Storage drift detected" card in the owning Slack channel. The card
names the missing key, reports checked/drifted/error counts, and links AWS
Service Health as advisory context. Repeated verifier ticks do not post the
same issue again because `slack_drift_alerts` claims the issue key durably.

## Profile Timing Demo Shapes

Use profiling when an investor asks, "Where did the time go?" The answer should
separate measured app spans from opaque provider/network spans.

Slack:

```text
@Nimbus status --profile-timings=hud
@Nimbus save all files in this channel --profile-timings=waterfall
@Nimbus summarize this channel --profile-timings=full
```

CLI:

```shell
uv run nimbus chat "list files under demo/" --profile local --profile-timings hud
uv run nimbus resume "verify that manifest" --profile local --profile-timings full
```

HALF is the executive budget:

```text
Nimbus profile HALF  trace=trc_123  total=1.284s

critical path
slack.parse_command             2 ms   measured   deterministic adapter parse
slack.command.save_files      421 ms   measured   scan/download/upload branch
s3.head_or_put                156 ms   measured   boto3 call boundary
slack.post_result             221 ms   opaque     Slack API boundary
round_trip                  1.284 s    measured   event received -> final post

resources
tool_calls                       1
bytes_from_s3               65,536
retries                          0
fallback                     false
```

FULL is the diagnostic trace tree:

```text
Nimbus profile FULL  trace=trc_123  total=1.284s

span                             ms   self   kind       notes
slack.events                     38     38   measured   ack path
  slack.signature_verify          1      1   measured
  slack.dedupe_claim              3      3   measured
slack.background              1246     14   measured
  slack.command_parse             1      1   measured
  slack.command.save_files      843      7   measured
    slack.files_list            112     12   opaque     Slack SDK/API
    s3.put_object               156      8   opaque     boto3/network/S3
    manifest.persist              5      5   measured
  slack.post_result             221    221   opaque     Slack chat.update

opaque boundaries: kernel buffers, TLS internals, Slack/AWS provider internals.
```

HUD is the room-friendly view:

```text
Nimbus profile HUD  trace=trc_123  total=1.284s

slack ack       [###-----------------]   38 ms
adapter work    [#####---------------]  112 ms
runtime/model   [############--------]  741 ms
aws storage     [####----------------]  156 ms
slack post      [######--------------]  221 ms

bottleneck: runtime/model at 58% of wall time
```

WATERFALL shows offset and overlap:

```text
Nimbus profile WATERFALL  trace=trc_123  total=1.284s

offset      span                          duration
+0000 ms    slack.events                     38 ms
+0039 ms    slack.background              1246 ms
+0041 ms      slack.command_parse             1 ms
+0043 ms      slack.command.save_files      843 ms
+0888 ms      slack.post_result             221 ms
+1284 ms    final reply visible
```

## What Not To Overclaim

- Slack does not scan arbitrary S3 uploads for duplicates yet; it checks
  Nimbus-saved Slack manifests.
- Slack proactively alerts for drift in Nimbus-saved Slack manifests. It does
  not yet watch arbitrary S3 prefixes that were never saved or protected by
  Nimbus.
- The formal artifacts are TLA+/Lean source plus Python drift tests. This local
  machine has Lean and TLC installed outside the repo; the repo does not vendor
  those toolchains.
