from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.analytics_contracts import AnalyticsError
from backend.analytics_grants import GrantStore
from tests.analytics_support import Clock, FakeIssuer, record


def test_single_use_concurrent_exchange_and_hashed_storage():
    clock = Clock()
    store = GrantStore(clock=clock)
    issuer = FakeIssuer({"code": record(clock)})
    def attempt(_):
        try:
            return store.exchange("code", issuer)[0]
        except AnalyticsError as error:
            return error.code
    with ThreadPoolExecutor(8) as workers:
        outcomes = list(workers.map(attempt, range(8)))
    assert outcomes.count("ACCESS_GRANT_INVALID") == 7
    token = next(item for item in outcomes if item != "ACCESS_GRANT_INVALID")
    assert token not in store._grants and "code" not in store._used
    assert list(store._grants) == [store.digest(token)]
    assert "FAKE-UPSTREAM-CAPABILITY" not in repr(next(iter(store._grants.values())))


@pytest.mark.parametrize("age,accepted", [(0, True), (59, True), (60, False), (-1, False)])
def test_exchange_code_deadline(age, accepted):
    clock = Clock()
    store = GrantStore(clock=clock)
    issuer = FakeIssuer({"code": record(clock, issued_at=int(clock())-age)})
    if accepted:
        store.exchange("code", issuer)
    else:
        with pytest.raises(AnalyticsError):
            store.exchange("code", issuer)


@pytest.mark.parametrize("changes", [
    {"audience": "wrong"}, {"origin": "http://localhost:5173"},
    {"scopes": frozenset({"results:read"})}, {"scopes": frozenset({"summary:read"})},
    {"scopes": frozenset({"summary:read", "admin"})}, {"dataset_id": "../escape"},
    {"upstream_capability": ""},
])
def test_invalid_authority_record_is_not_broadened(changes):
    clock = Clock()
    issuer = FakeIssuer({"code": record(clock, **changes)})
    store = GrantStore(clock=clock)
    with pytest.raises(AnalyticsError) as caught:
        store.exchange("code", issuer)
    assert caught.value.code == "INVALID_ACCESS_REQUEST"
    assert not store._grants
    assert issuer.consume("code") is None


def test_expiry_revocation_restart_and_lost_exchange_response():
    clock = Clock()
    issuer = FakeIssuer({"code": record(clock), "second": record(clock)})
    store = GrantStore(clock=clock)
    token, grant, stamp = store.exchange("code", issuer)
    assert grant.expires_at == stamp+600
    clock.value += 599
    assert store.authorize(token, "dataset-a") == grant
    assert grant.expires_at == stamp+600  # no sliding renewal
    clock.value += 1
    with pytest.raises(AnalyticsError):
        store.authorize(token, "dataset-a")
    clock.value -= 600
    token, _, _ = store.exchange("second", issuer)
    assert store.revoke(token)
    with pytest.raises(AnalyticsError):
        store.authorize(token, "dataset-a")
    restarted = GrantStore(clock=clock)
    with pytest.raises(AnalyticsError):
        restarted.authorize(token, "dataset-a")
    with pytest.raises(AnalyticsError):
        restarted.exchange("second", issuer)


def test_scope_intersection_and_frozen_results():
    clock = Clock()
    store = GrantStore(clock=clock)
    token, grant, _ = store.exchange("code", FakeIssuer({"code": record(clock)}))
    for dataset, result in [("other", None), ("dataset-a", "other")]:
        with pytest.raises(AnalyticsError) as caught:
            store.authorize(token, dataset, results=True, result_id=result)
        assert caught.value.status == 403
    assert store.authorize(token, "dataset-a", results=True, result_id="result-a") == grant
    token, _, _ = store.exchange("summary", FakeIssuer({"summary": record(clock,
        scopes=frozenset({"summary:read"}), result_ids=frozenset())}))
    store.authorize(token, "dataset-a")
    with pytest.raises(AnalyticsError):
        store.authorize(token, "dataset-a", results=True)


def test_issuer_latency_cannot_redeem_expired_code():
    clock = Clock()
    class Slow(FakeIssuer):
        def consume(self, code):
            result = super().consume(code)
            clock.value += 60
            return result
    with pytest.raises(AnalyticsError):
        GrantStore(clock=clock).exchange("code", Slow({"code": record(clock)}))


def test_capacity_fails_explicitly_without_consuming_code():
    clock = Clock()
    issuer = FakeIssuer({"code": record(clock)})
    with pytest.raises(AnalyticsError) as caught:
        GrantStore(clock=clock, capacity=0).exchange("code", issuer)
    assert caught.value.status == 503 and "code" in issuer.records
