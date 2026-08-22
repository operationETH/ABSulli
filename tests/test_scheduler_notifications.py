import asyncio

import absulli.monitors.scheduler as scheduler_module
from absulli.monitors.scheduler import AbsulliScheduler


class FakeNotifier:
    def __init__(self):
        self.calls = []

    async def notify(self, db, event_type, title, body, **kwargs):
        self.calls.append((db, event_type, title, body, kwargs))


def make_scheduler():
    scheduler = object.__new__(AbsulliScheduler)
    scheduler.notifier = FakeNotifier()
    return scheduler


def test_reachability_transition_sends_failed_once(monkeypatch):
    store = {"abs_reachable": "true"}
    monkeypatch.setattr(scheduler_module, "get_setup_setting", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(scheduler_module, "set_setup_setting", lambda key, value: store.__setitem__(key, value))
    scheduler = make_scheduler()
    db = object()

    asyncio.run(scheduler._record_abs_reachable(db, False))
    asyncio.run(scheduler._record_abs_reachable(db, False))

    assert [call[1] for call in scheduler.notifier.calls] == ["abs_connection_failed"]
    assert store["abs_reachable"] == "false"
    assert "abs_last_failure_at" in store


def test_reachability_transition_sends_restored_once(monkeypatch):
    store = {"abs_reachable": "false"}
    monkeypatch.setattr(scheduler_module, "get_setup_setting", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(scheduler_module, "set_setup_setting", lambda key, value: store.__setitem__(key, value))
    scheduler = make_scheduler()
    db = object()

    asyncio.run(scheduler._record_abs_reachable(db, True))
    asyncio.run(scheduler._record_abs_reachable(db, True))

    assert [call[1] for call in scheduler.notifier.calls] == ["abs_connection_restored"]
    assert store["abs_reachable"] == "true"
    assert "abs_last_success_at" in store


def test_initial_reachability_state_does_not_send_notification(monkeypatch):
    store = {}
    monkeypatch.setattr(scheduler_module, "get_setup_setting", lambda key, default="": store.get(key, default))
    monkeypatch.setattr(scheduler_module, "set_setup_setting", lambda key, value: store.__setitem__(key, value))
    scheduler = make_scheduler()

    asyncio.run(scheduler._record_abs_reachable(object(), False))

    assert scheduler.notifier.calls == []
    assert store["abs_reachable"] == "false"
