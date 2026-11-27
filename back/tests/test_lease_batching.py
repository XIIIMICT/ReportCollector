"""Fencing-token invariants that must survive batched lease traffic."""

from src.collab.store import lease_key


async def test_fencing_tokens_stay_monotonic_across_batch_lease_calls(store) -> None:
    keys = [
        lease_key(f"rm_material_movements/cell-{index}/demandPlanTons", project="raw_materials") for index in range(3)
    ]
    owner = {"userId": "u1", "sessionId": "s1", "name": "First"}

    previous = await store.next_action_id()
    action_id = await store.next_action_id()
    assert action_id > previous

    try:
        revisions: list[int] = []
        for _ in range(3):
            assert await store.acquire_leases(keys, owner) == [None, None, None]
            assert await store.release_leases(keys, owner) == [True, True, True]
            next_id = await store.next_action_id()
            assert next_id > action_id
            action_id = next_id
            bumps = await store.bump_cell_states([keys[0]], user_id="u1", user_name="First", action_id=action_id)
            revisions.append(bumps[keys[0]])

        assert revisions == [1, 2, 3]
        state = await store.get_cell_state(keys[0])
        assert state is not None
        assert state["lastActionId"] == str(action_id)
        assert state["lastUserId"] == "u1"
    finally:
        await store.release_leases(keys, owner)
