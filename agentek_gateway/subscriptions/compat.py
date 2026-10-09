import enum
import sys
from typing import NoReturn

if sys.version_info >= (3, 11):
    from enum import StrEnum
    from typing import assert_never
else:

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

    def assert_never(value: object) -> "NoReturn":
        raise AssertionError(f"unhandled value: {value!r}")


__all__ = ["StrEnum", "assert_never"]
