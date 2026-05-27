---- MODULE NimbusActionLedger ----
EXTENDS Naturals, Sequences

VARIABLES actionStatus, approvalStatus

\* This MVP spec pins the public status vocabulary and the smallest
\* action/approval transition kernel Nimbus must preserve. The Python test
\* suite checks these constants against runtime_status_spec().

ActionStatuses ==
  {
    "proposed",
    "awaiting_confirmation",
    "authorized",
    "queued",
    "executing",
    "verifying",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "expired",
    "cancelled"
  }

ApprovalStatuses ==
  {
    "pending",
    "approved",
    "rejected",
    "expired"
  }

GenerationStatuses ==
  {
    "complete",
    "partial",
    "failed"
  }

StackStatuses ==
  {
    "proposed",
    "approved",
    "applied",
    "abandoned",
    "conflicted",
    "failed"
  }

TerminalActionStatuses ==
  {
    "succeeded",
    "failed_terminal",
    "expired",
    "cancelled"
  }

ActionStep(from, to) ==
  \/ /\ from = "proposed"
     /\ to \in {"awaiting_confirmation", "authorized", "expired", "cancelled"}
  \/ /\ from = "awaiting_confirmation"
     /\ to \in {"authorized", "expired", "cancelled"}
  \/ /\ from = "authorized"
     /\ to \in {"queued", "expired", "cancelled"}
  \/ /\ from = "queued"
     /\ to \in {"executing", "cancelled"}
  \/ /\ from = "executing"
     /\ to \in {"verifying", "failed_retryable", "failed_terminal"}
  \/ /\ from = "verifying"
     /\ to \in {"succeeded", "failed_retryable", "failed_terminal"}
  \/ /\ from = "failed_retryable"
     /\ to \in {"queued", "failed_terminal", "cancelled"}

ApprovalStep(from, to) ==
  /\ from = "pending"
  /\ to \in {"approved", "rejected", "expired"}

NoTerminalActionStep ==
  \A s \in TerminalActionStatuses:
    \A t \in ActionStatuses:
      ~ActionStep(s, t)

ApprovalDecisionIsTerminal ==
  \A s \in {"approved", "rejected", "expired"}:
    \A t \in ApprovalStatuses:
      ~ApprovalStep(s, t)

Init ==
  /\ actionStatus = "proposed"
  /\ approvalStatus = "pending"

Next ==
  \/ \E next \in ActionStatuses:
       /\ ActionStep(actionStatus, next)
       /\ actionStatus' = next
       /\ UNCHANGED approvalStatus
  \/ \E next \in ApprovalStatuses:
       /\ ApprovalStep(approvalStatus, next)
       /\ approvalStatus' = next
       /\ UNCHANGED actionStatus

Spec ==
  /\ Init
  /\ [][Next]_<<actionStatus, approvalStatus>>

ActionStatusValid ==
  actionStatus \in ActionStatuses

ApprovalStatusValid ==
  approvalStatus \in ApprovalStatuses

====
