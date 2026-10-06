import asyncio
import logging

from app.core.logging import redact, configure_logging
from app.core.runtime import TaskRegistry


def test_redact_covers_common_secret_forms():
    text = "password=abc123 token: xyz --password hunter2 Authorization: Bearer SECRET"
    out = redact(text)
    assert "abc123" not in out
    assert "xyz" not in out
    assert "hunter2" not in out
    assert "SECRET" not in out
    assert "<redacted>" in out


def test_task_registry_registers_and_removes_completed_task():
    async def scenario():
        registry = TaskRegistry()
        task = registry.create(asyncio.sleep(0), name="test-task", owner="test")
        assert task is not None
        await task
        await asyncio.sleep(0)
        assert not registry.snapshot()

    asyncio.run(scenario())


def test_task_registry_audit_finds_unregistered_task():
    async def scenario():
        registry = TaskRegistry()
        leaked = asyncio.create_task(asyncio.sleep(0.1), name="deliberate-unregistered")
        found = registry.audit(include_internal=True)
        assert leaked in found
        leaked.cancel()
        await asyncio.gather(leaked, return_exceptions=True)

    asyncio.run(scenario())


def test_logging_is_idempotent():
    path = configure_logging(logging.INFO)
    assert path.name == "sshdock.log"
    path2 = configure_logging(logging.INFO)
    assert path2 == path
