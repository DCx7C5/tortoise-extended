# pyright: reportExplicitAny=false
"""Stub for ``tortoise.validators``.

The runtime module uses ``from __future__ import annotations`` which prevents
pyright from resolving ``Validator`` as a concrete class.
"""

import abc
import re
from abc import ABC
from typing import Any, override

class Validator(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def __call__(self, value: Any) -> None: ...

class RegexValidator(Validator):
    regex: Any
    flags: int | re.RegexFlag
    def __init__(self, pattern: str, flags: int | re.RegexFlag) -> None: ...
    @override
    def __call__(self, value: Any) -> None: ...

class MaxLengthValidator(Validator):
    max_length: int
    def __init__(self, max_length: int) -> None: ...
    @override
    def __call__(self, value: str) -> None: ...

class MinLengthValidator(Validator):
    min_length: int
    def __init__(self, min_length: int) -> None: ...
    @override
    def __call__(self, value: str) -> None: ...

class NumericValidator(Validator, ABC):
    types: tuple[type, ...]
    def _validate_type(self, value: Any) -> None: ...

class MinValueValidator(NumericValidator):
    min_value: int | float
    def __init__(self, min_value: float) -> None: ...
    @override
    def __call__(self, value: float) -> None: ...

class MaxValueValidator(NumericValidator):
    max_value: int | float
    def __init__(self, max_value: float) -> None: ...
    @override
    def __call__(self, value: float) -> None: ...

class CommaSeparatedIntegerListValidator(Validator):
    regex: RegexValidator
    def __init__(self, allow_negative: bool = False) -> None: ...
    @override
    def __call__(self, value: str) -> None: ...

def validate_ipv4_address(value: Any) -> None: ...
def validate_ipv6_address(value: Any) -> None: ...
def validate_ipv46_address(value: Any) -> None: ...
