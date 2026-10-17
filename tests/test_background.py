import threading

import pytest

from printlog_orca.background import Background


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def background():
    created = []

    def make(**kwargs):
        bg = Background(**kwargs)
        bg.start()
        created.append(bg)
        return bg

    yield make
    for bg in created:
        bg.stop()


def test_runs_submitted_tasks_in_order_on_its_own_thread(background):
    bg = background()
    seen = []

    bg.submit(lambda: seen.append(("a", threading.current_thread().name)))
    bg.submit(lambda: seen.append(("b", threading.current_thread().name)))

    assert bg.wait_idle()
    assert seen == [("a", "3D Print Log"), ("b", "3D Print Log")]


def test_a_failing_task_does_not_stop_the_thread(background):
    bg = background()
    seen = []

    bg.submit(lambda: 1 / 0)
    bg.submit(lambda: seen.append("after"))

    assert bg.wait_idle()
    assert seen == ["after"]


def test_notify_later_keeps_only_the_last_of_a_burst(background):
    bg = background(notify_delay=0.05)
    seen = []
    gate = threading.Event()
    bg.submit(gate.wait)  # hold the thread so the burst lands together

    for n in range(5):
        bg.notify_later(lambda n=n: seen.append(n))
    gate.set()

    assert bg.wait_idle()
    assert seen == [4]


def test_notify_later_waits_for_the_delay():
    clock = Clock()
    bg = Background(notify_delay=60, clock=clock)
    bg.start()
    seen = []
    try:
        bg.notify_later(lambda: seen.append("shown"))

        assert not bg.wait_idle(timeout=0.2)
        assert seen == []
    finally:
        bg.stop()


def test_tasks_run_before_a_pending_notification(background):
    bg = background(notify_delay=0)
    seen = []
    gate = threading.Event()
    bg.submit(gate.wait)

    bg.notify_later(lambda: seen.append("notify"))
    bg.submit(lambda: seen.append("upload"))
    gate.set()

    assert bg.wait_idle()
    assert seen == ["upload", "notify"]


def test_a_stopped_thread_refuses_work(background):
    bg = background()
    bg.stop()

    assert bg.submit(lambda: None) is False
    assert bg.notify_later(lambda: None) is False
    assert not bg.running()


def test_submit_never_waits_for_running_work(background):
    bg = background()
    gate = threading.Event()
    bg.submit(gate.wait)

    assert bg.submit(lambda: None) is True  # returns while the first task still blocks
    gate.set()
    assert bg.wait_idle()
