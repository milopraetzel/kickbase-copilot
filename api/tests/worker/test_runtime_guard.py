"""Der produktive Scheduler startet nur im dafür markierten Container."""

import pytest

from kickbase_agent.worker import __main__ as worker_main


def test_worker_refuses_unmarked_runtime_before_scheduler(monkeypatch):
    monkeypatch.delenv("KBA_WORKER_RUNTIME", raising=False)
    monkeypatch.setattr(worker_main.sys, "argv", ["kba-worker"])

    def forbidden_run(_coro):
        pytest.fail("Der Scheduler darf lokal nicht starten")

    monkeypatch.setattr(worker_main.asyncio, "run", forbidden_run)

    with pytest.raises(SystemExit, match="Server-Container"):
        worker_main.main()


@pytest.mark.parametrize(
    ("runtime", "argv"),
    [("box", ["kba-worker"]), (None, ["kba-worker", "--local-dev"])],
)
def test_worker_allows_box_or_explicit_local_development(monkeypatch, runtime, argv):
    if runtime is None:
        monkeypatch.delenv("KBA_WORKER_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("KBA_WORKER_RUNTIME", runtime)
    monkeypatch.setattr(worker_main.sys, "argv", argv)
    started = []

    def record_run(coro):
        started.append(True)
        coro.close()

    monkeypatch.setattr(worker_main.asyncio, "run", record_run)

    worker_main.main()

    assert started == [True]
