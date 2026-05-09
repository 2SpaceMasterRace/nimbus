"""Property-based tests for Nimbus runtime safety invariants."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nimbus_runtime.domain import (
    Action,
    ActionKind,
    ActionStatus,
    ActionTransition,
    Approval,
    ApprovalChoice,
    ApprovalStatus,
    DeleteFileInput,
    ObjectRef,
    PlanRiskLevel,
    TenantIdentity,
    VerifiedActor,
    is_valid_action_transition,
    is_valid_approval_transition,
    validate_action_transition,
    validate_approval_transition,
)
from nimbus_runtime.search import (
    FileSearchIndexStore,
    SearchActorScope,
    SearchChunk,
    SearchDocument,
    SearchDocumentStatus,
    SearchQuery,
)
from nimbus_runtime.stores import (
    FileActionStore,
    FileApprovalStore,
    FileSessionEventStore,
)

pytestmark = pytest.mark.property

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SAFE_ID = st.from_regex(r"^[A-Za-z0-9_.:-]{1,24}$", fullmatch=True)
_SAFE_PATH = st.from_regex(
    r"^[A-Za-z0-9_.:-]{1,12}/[A-Za-z0-9_.:-]{1,24}$", fullmatch=True
)
_STATUS = st.sampled_from(tuple(ActionStatus))
_APPROVAL_STATUS = st.sampled_from(tuple(ApprovalStatus))


def _tenant(workspace_id: str = "T123TEAM") -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id=workspace_id)


def _actor(
    tenant: TenantIdentity,
    *,
    user_id: str = "U123USER",
) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id=user_id,
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=_NOW,
    )


def _delete_action(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    action_id: str,
    idempotency_key: str,
    status: ActionStatus = ActionStatus.AWAITING_CONFIRMATION,
    remote_path: str = "reports/old.csv",
) -> Action:
    return Action(
        action_id=action_id,
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.DELETE_FILE,
        target=ObjectRef(
            provider="s3",
            container="bucket",
            object_name=remote_path,
        ),
        status=status,
        idempotency_key=idempotency_key,
        input=DeleteFileInput(remote_path=remote_path),
        result=None,
        failure=None,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=_NOW + timedelta(minutes=15),
    )


def _approval(
    *,
    tenant: TenantIdentity,
    requested_by: VerifiedActor,
    allowed_actor_ids: tuple[str, ...],
    exact_target: str,
    expires_at: datetime,
    approval_id: str = "appr-test",
    idempotency_key: str = "idem-approval",
) -> Approval:
    return Approval(
        approval_id=approval_id,
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        task_id=None,
        plan_id="plan-test",
        action_id="act-test",
        requested_by=requested_by,
        required_actor_id=requested_by.user_id,
        allowed_actor_ids=allowed_actor_ids,
        status=ApprovalStatus.PENDING,
        risk_level=PlanRiskLevel.DESTRUCTIVE,
        exact_target=exact_target,
        reason="delete_file_requires_exact_actor_bound_approval",
        idempotency_key=idempotency_key,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=expires_at,
    )


def _document(
    *,
    tenant: TenantIdentity,
    document_id: str,
    text_token: str,
    channel_id: str,
    actor_ids: tuple[str, ...],
    channel_ids: tuple[str, ...],
) -> tuple[SearchDocument, SearchChunk]:
    document = SearchDocument(
        tenant=tenant,
        document_id=document_id,
        source_uri=f"slack://{tenant.workspace_id}/{channel_id}/{document_id}",
        object_key=f"channels/{channel_id}/{document_id}.txt",
        title=f"Document {document_id}",
        content_type="text/plain",
        size_bytes=128,
        status=SearchDocumentStatus.SEARCHABLE,
        workspace_id=tenant.workspace_id,
        channel_id=channel_id,
        uploader_id="UUPLOAD",
        bucket="demo-bucket",
        saved=False,
        sha256_hex=f"{document_id:0<64}"[:64],
        source_created_at=_NOW,
        indexed_at=_NOW,
        visible_to_actor_ids=actor_ids,
        visible_to_channel_ids=channel_ids,
        metadata={"source": "property-test"},
    )
    chunk = SearchChunk(
        tenant=tenant,
        document_id=document_id,
        chunk_id=f"chunk-{document_id}",
        chunk_index=0,
        text=f"{text_token} storage evidence for {document_id}",
        page_number=1,
        start_offset=0,
        end_offset=len(text_token),
    )
    return document, chunk


@given(_STATUS, _STATUS)
def test_action_transition_validator_matches_transition_oracle(
    expected: ActionStatus,
    next_status: ActionStatus,
) -> None:
    """The validator and boolean transition oracle must agree for every pair."""
    allowed = is_valid_action_transition(
        expected=expected,
        next_status=next_status,
    )
    if allowed:
        validate_action_transition(expected=expected, next_status=next_status)
    else:
        with pytest.raises(ValueError, match="invalid action transition"):
            validate_action_transition(expected=expected, next_status=next_status)


@given(_APPROVAL_STATUS, _APPROVAL_STATUS)
def test_approval_transition_validator_matches_transition_oracle(
    expected: ApprovalStatus,
    next_status: ApprovalStatus,
) -> None:
    """Approval transition validation must exactly match its oracle."""
    allowed = is_valid_approval_transition(
        expected=expected,
        next_status=next_status,
    )
    if allowed:
        validate_approval_transition(expected=expected, next_status=next_status)
    else:
        with pytest.raises(ValueError, match="invalid approval transition"):
            validate_approval_transition(expected=expected, next_status=next_status)


@given(_STATUS)
def test_terminal_action_statuses_have_no_outgoing_transitions(
    terminal_status: ActionStatus,
) -> None:
    """Terminal action states must remain terminal under the transition oracle."""
    terminal = {
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED_TERMINAL,
        ActionStatus.EXPIRED,
        ActionStatus.CANCELLED,
    }
    if terminal_status not in terminal:
        return
    assert all(
        not is_valid_action_transition(
            expected=terminal_status,
            next_status=next_status,
        )
        for next_status in ActionStatus
    )


@given(
    action_id=_SAFE_ID,
    idempotency_key=_SAFE_ID,
    remote_path=_SAFE_PATH,
)
@settings(max_examples=25, deadline=None)
def test_file_action_store_is_idempotent_and_compare_and_set(
    action_id: str,
    idempotency_key: str,
    remote_path: str,
) -> None:
    """Action creation is once-per-key and transitions require the expected state."""
    tenant = _tenant()
    actor = _actor(tenant)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        event_store = FileSessionEventStore(root)
        action_store = FileActionStore(root, event_store=event_store)
        calls = 0

        def create() -> Action:
            nonlocal calls
            calls += 1
            return _delete_action(
                tenant=tenant,
                actor=actor,
                action_id=action_id,
                idempotency_key=idempotency_key,
                remote_path=remote_path,
            )

        first = action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )
        second = action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )
        assert second == first
        assert calls == 1

        failed = action_store.transition(
            tenant=tenant,
            action_id=action_id,
            transition=ActionTransition(
                expected=ActionStatus.PROPOSED,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={"source": "property"},
            ),
        )
        assert failed is None
        assert (
            action_store.get(tenant=tenant, action_id=action_id).status
            is ActionStatus.AWAITING_CONFIRMATION
        )

        updated = action_store.transition(
            tenant=tenant,
            action_id=action_id,
            transition=ActionTransition(
                expected=ActionStatus.AWAITING_CONFIRMATION,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={"source": "property"},
            ),
        )
        assert updated is not None
        assert updated.status is ActionStatus.AUTHORIZED
        events = event_store.list_events(tenant=tenant, session_id=first.session_id)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert [event.event_type for event in events] == [
            "action_created",
            "action_authorized",
        ]


@given(
    allowed_delegate=_SAFE_ID,
    deciding_user=_SAFE_ID,
    exact_target=_SAFE_PATH,
    provided_target=_SAFE_PATH,
    choice=st.sampled_from(tuple(ApprovalChoice)),
    expired=st.booleans(),
    other_tenant=st.booleans(),
)
@settings(max_examples=35, deadline=None)
def test_approval_decision_accepts_only_bound_actor_target_and_live_approval(
    allowed_delegate: str,
    deciding_user: str,
    exact_target: str,
    provided_target: str,
    choice: ApprovalChoice,
    expired: bool,
    other_tenant: bool,
) -> None:
    """Approval decisions enforce actor, tenant, expiry, and exact target binding."""
    tenant = _tenant()
    requester = _actor(tenant, user_id="UREQUESTER")
    decision_tenant = _tenant("T999TEAM") if other_tenant else tenant
    decider = _actor(decision_tenant, user_id=deciding_user)
    expires_at = _NOW - timedelta(seconds=1) if expired else _NOW + timedelta(minutes=5)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        approval_store = FileApprovalStore(
            root,
            event_store=FileSessionEventStore(root),
        )
        approval = approval_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key="idem-approval",
            create=lambda: _approval(
                tenant=tenant,
                requested_by=requester,
                allowed_actor_ids=(allowed_delegate,),
                exact_target=exact_target,
                expires_at=expires_at,
            ),
        )

        result = approval_store.decide(
            tenant=tenant,
            approval_id=approval.approval_id,
            actor=decider,
            choice=choice,
            exact_target=provided_target,
            now=_NOW,
        )

        actor_allowed = deciding_user in {requester.user_id, allowed_delegate}
        should_accept = (
            not other_tenant
            and actor_allowed
            and not expired
            and provided_target == exact_target
        )
        assert result.accepted is should_accept
        if should_accept:
            assert result.approval is not None
            assert result.approval.decided_by == decider
            assert result.approval.status is (
                ApprovalStatus.APPROVED
                if choice is ApprovalChoice.APPROVE
                else ApprovalStatus.REJECTED
            )
        else:
            assert result.reason in {
                "tenant_mismatch",
                "wrong_actor",
                "expired",
                "target_mismatch",
            }
            stored = approval_store.get(
                tenant=tenant,
                approval_id=approval.approval_id,
            )
            assert stored is not None
            if not other_tenant and actor_allowed and expired:
                assert stored.status is ApprovalStatus.EXPIRED
            else:
                assert stored.status is ApprovalStatus.PENDING


@given(
    visible_by_channel=st.booleans(),
    visible_by_actor=st.booleans(),
    workspace_wide=st.booleans(),
    query_token=st.from_regex(r"^[A-Za-z][A-Za-z0-9]{0,10}$", fullmatch=True),
)
@settings(max_examples=25, deadline=None)
def test_search_results_are_subset_of_tenant_and_acl_visible_documents(
    visible_by_channel: bool,
    visible_by_actor: bool,
    workspace_wide: bool,
    query_token: str,
) -> None:
    """Search must apply tenant and ACL filtering before lexical ranking."""
    tenant = _tenant()
    actor = _actor(tenant)
    other_tenant = _tenant("T999TEAM")
    visible_channels = frozenset({"COPEN"}) if visible_by_channel else frozenset()
    channel_acl = ("COPEN",) if visible_by_channel else ("CPRIVATE",)
    actor_acl = (actor.user_id,) if visible_by_actor else ()

    with tempfile.TemporaryDirectory() as tmp:
        store = FileSearchIndexStore(Path(tmp))
        allowed_document, allowed_chunk = _document(
            tenant=tenant,
            document_id="doc-allowed",
            text_token=query_token,
            channel_id="COPEN",
            actor_ids=actor_acl,
            channel_ids=channel_acl,
        )
        secret_document, secret_chunk = _document(
            tenant=tenant,
            document_id="doc-secret",
            text_token=query_token,
            channel_id="CSECRET",
            actor_ids=(),
            channel_ids=("CSECRET",),
        )
        cross_document, cross_chunk = _document(
            tenant=other_tenant,
            document_id="doc-cross",
            text_token=query_token,
            channel_id="COPEN",
            actor_ids=(actor.user_id,),
            channel_ids=("COPEN",),
        )
        for document, chunk in (
            (allowed_document, allowed_chunk),
            (secret_document, secret_chunk),
            (cross_document, cross_chunk),
        ):
            store.index_document(document=document, chunks=(chunk,))

        results = store.search(
            scope=SearchActorScope(
                actor=actor,
                visible_channel_ids=visible_channels,
                workspace_wide=workspace_wide,
            ),
            query=SearchQuery(text=query_token, limit=10),
        )

    result_ids = {result.document.document_id for result in results}
    if workspace_wide:
        expected_ids = {"doc-allowed", "doc-secret"}
    else:
        allowed_visible = visible_by_channel or visible_by_actor
        expected_ids = {"doc-allowed"} if allowed_visible else set()
    assert result_ids == expected_ids
    assert "doc-cross" not in result_ids
    assert all(result.document.tenant == tenant for result in results)


@given(
    token_pair=st.sampled_from(
        (
            ("AlphaOnly", "BetaOnly"),
            ("GammaOnly", "DeltaOnly"),
            ("LedgerOnly", "ProjectionOnly"),
        )
    ),
)
@settings(max_examples=25, deadline=None)
def test_search_reindex_replaces_old_chunks_atomically(
    token_pair: tuple[str, str],
) -> None:
    """Re-indexing one document must replace stale chunks, not append to them."""
    old_token, new_token = token_pair
    tenant = _tenant()
    actor = _actor(tenant)
    with tempfile.TemporaryDirectory() as tmp:
        store = FileSearchIndexStore(Path(tmp))
        document, old_chunk = _document(
            tenant=tenant,
            document_id="doc-reindexed",
            text_token=old_token,
            channel_id="COPEN",
            actor_ids=(),
            channel_ids=("COPEN",),
        )
        _, new_chunk = _document(
            tenant=tenant,
            document_id="doc-reindexed",
            text_token=new_token,
            channel_id="COPEN",
            actor_ids=(),
            channel_ids=("COPEN",),
        )
        scope = SearchActorScope(
            actor=actor,
            visible_channel_ids=frozenset({"COPEN"}),
        )

        store.index_document(document=document, chunks=(old_chunk,))
        assert store.search(scope=scope, query=SearchQuery(text=old_token))

        store.index_document(document=document, chunks=(new_chunk,))
        old_results = store.search(scope=scope, query=SearchQuery(text=old_token))
        new_results = store.search(scope=scope, query=SearchQuery(text=new_token))

    assert old_results == ()
    assert [result.document.document_id for result in new_results] == ["doc-reindexed"]
    assert all(
        new_token in hit.snippet for result in new_results for hit in result.chunk_hits
    )


@given(
    document_count=st.integers(min_value=2, max_value=8),
    limit=st.integers(min_value=1, max_value=8),
    query_token=st.from_regex(r"^[A-Za-z][A-Za-z0-9]{0,10}$", fullmatch=True),
)
@settings(max_examples=20, deadline=None)
def test_search_limit_is_applied_after_acl_filtering(
    document_count: int,
    limit: int,
    query_token: str,
) -> None:
    """Search limits should cap only ACL-visible tenant-local results."""
    tenant = _tenant()
    actor = _actor(tenant)
    expected_count = min(document_count, limit)
    with tempfile.TemporaryDirectory() as tmp:
        store = FileSearchIndexStore(Path(tmp))
        for index in range(document_count):
            document, chunk = _document(
                tenant=tenant,
                document_id=f"doc-visible-{index}",
                text_token=query_token,
                channel_id="COPEN",
                actor_ids=(),
                channel_ids=("COPEN",),
            )
            store.index_document(document=document, chunks=(chunk,))
            hidden, hidden_chunk = _document(
                tenant=tenant,
                document_id=f"doc-hidden-{index}",
                text_token=query_token,
                channel_id="CSECRET",
                actor_ids=(),
                channel_ids=("CSECRET",),
            )
            store.index_document(document=hidden, chunks=(hidden_chunk,))

        results = store.search(
            scope=SearchActorScope(
                actor=actor,
                visible_channel_ids=frozenset({"COPEN"}),
            ),
            query=SearchQuery(text=query_token, limit=limit),
        )

    assert len(results) == expected_count
    assert all(result.document.channel_id == "COPEN" for result in results)
    assert all(result.document.tenant == tenant for result in results)


@given(_SAFE_ID, _SAFE_ID)
@settings(max_examples=25, deadline=None)
def test_action_store_rejects_idempotency_key_or_tenant_mismatch(
    stored_key: str,
    call_key: str,
) -> None:
    """The store must not persist an action whose boundary key or tenant differs."""
    tenant = _tenant()
    actor = _actor(tenant)
    action_tenant = _tenant("T999TEAM")
    with tempfile.TemporaryDirectory() as tmp:
        store = FileActionStore(Path(tmp))
        if stored_key != call_key:
            with pytest.raises(ValueError, match="idempotency key"):
                store.create_or_get_by_idempotency(
                    tenant=tenant,
                    idempotency_key=call_key,
                    create=lambda: _delete_action(
                        tenant=tenant,
                        actor=actor,
                        action_id="act-test",
                        idempotency_key=stored_key,
                    ),
                )
        with pytest.raises(ValueError, match="tenant"):
            store.create_or_get_by_idempotency(
                tenant=tenant,
                idempotency_key="idem-cross-tenant",
                create=lambda: replace(
                    _delete_action(
                        tenant=tenant,
                        actor=actor,
                        action_id="act-cross-tenant",
                        idempotency_key="idem-cross-tenant",
                    ),
                    tenant=action_tenant,
                ),
            )
