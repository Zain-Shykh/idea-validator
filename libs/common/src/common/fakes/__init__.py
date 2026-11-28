"""In-memory stand-ins for services that do not exist yet, for tests (M9 now, M3 in Z7)."""

from common.fakes.analyst import FakeAnalyst
from common.fakes.kb import FakeKB

__all__ = ["FakeAnalyst", "FakeKB"]
