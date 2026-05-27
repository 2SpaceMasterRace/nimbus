/-!
Nimbus action-ledger status kernel.

This Lean4 MVP mirrors the executable runtime status vocabulary and the
terminal-state invariants checked by the Python runtime tests. It is deliberately
small: the goal is to make illegal transition assumptions reviewable before
heavier formal models are worth their operational cost.
-/

namespace Nimbus

inductive ActionStatus where
  | proposed
  | awaiting_confirmation
  | authorized
  | queued
  | executing
  | verifying
  | succeeded
  | failed_retryable
  | failed_terminal
  | expired
  | cancelled
deriving DecidableEq, Repr

inductive ApprovalStatus where
  | pending
  | approved
  | rejected
  | expired
deriving DecidableEq, Repr

inductive GenerationStatus where
  | complete
  | «partial»
  | failed
deriving DecidableEq, Repr

inductive StackStatus where
  | proposed
  | approved
  | applied
  | abandoned
  | conflicted
  | failed
deriving DecidableEq, Repr

def ActionCanStep : ActionStatus -> ActionStatus -> Prop
  | .proposed, .awaiting_confirmation => True
  | .proposed, .authorized => True
  | .proposed, .expired => True
  | .proposed, .cancelled => True
  | .awaiting_confirmation, .authorized => True
  | .awaiting_confirmation, .expired => True
  | .awaiting_confirmation, .cancelled => True
  | .authorized, .queued => True
  | .authorized, .expired => True
  | .authorized, .cancelled => True
  | .queued, .executing => True
  | .queued, .cancelled => True
  | .executing, .verifying => True
  | .executing, .failed_retryable => True
  | .executing, .failed_terminal => True
  | .verifying, .succeeded => True
  | .verifying, .failed_retryable => True
  | .verifying, .failed_terminal => True
  | .failed_retryable, .queued => True
  | .failed_retryable, .failed_terminal => True
  | .failed_retryable, .cancelled => True
  | _, _ => False

def ApprovalCanStep : ApprovalStatus -> ApprovalStatus -> Prop
  | .pending, .approved => True
  | .pending, .rejected => True
  | .pending, .expired => True
  | _, _ => False

theorem succeeded_terminal (next : ActionStatus) :
    ActionCanStep .succeeded next -> False := by
  intro h
  cases next <;> contradiction

theorem failed_terminal_terminal (next : ActionStatus) :
    ActionCanStep .failed_terminal next -> False := by
  intro h
  cases next <;> contradiction

theorem expired_terminal (next : ActionStatus) :
    ActionCanStep .expired next -> False := by
  intro h
  cases next <;> contradiction

theorem cancelled_terminal (next : ActionStatus) :
    ActionCanStep .cancelled next -> False := by
  intro h
  cases next <;> contradiction

theorem approval_approved_terminal (next : ApprovalStatus) :
    ApprovalCanStep .approved next -> False := by
  intro h
  cases next <;> contradiction

theorem approval_rejected_terminal (next : ApprovalStatus) :
    ApprovalCanStep .rejected next -> False := by
  intro h
  cases next <;> contradiction

theorem approval_expired_terminal (next : ApprovalStatus) :
    ApprovalCanStep .expired next -> False := by
  intro h
  cases next <;> contradiction

end Nimbus
