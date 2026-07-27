"""Stub for ``tortoise.validators``.

The runtime module uses ``from __future__ import annotations`` which prevents
pyright from resolving ``Validator`` as a concrete class.
"""

import abc
from typing import Any

class Validator(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def __call__(self, value: Any) -> None: ...


class RegexValidator(Validator):
    regex: Any
    def __init__(self, pattern: str, flags: int) -> None: ...
    def __call__(self, value: Any) -> None: ...


class MaxLengthValidator(Validator):
    max_length: int
    def __init__(self, max_length: int) -> None: ...
    def __call__(self, value: str) -> None: ...


class MinLengthValidator(Validator):
    min_length: int
    def __init__(self, min_length: int) -> None: ...
    def __call__(self, value: str) -> None: ...


class NumericValidator(Validator):
    types: tuple[type, ...]
    def _validate_type(self, value: Any) -> None: ...


class MinValueValidator(NumericValidator):
    min_value: int | float
    def __init__(self, min_value: float) -> None: ...
    def __call__(self, value: float) -> None: ...


class MaxValueValidator(NumericValidator):
    max_value: int | float
    def __init__(self, max_value: float) -> None: ...
    def __call__(self, value: float) -> None: ...


class CommaSeparatedIntegerListValidator(Validator):
    regex: RegexValidator
    def __init__(self, allow_negative: bool = False) -> None: ...
    def __call__(self, value: str) -> None: ...


def validate_ipv4_address(value: Any) -> None: ...
def validate_ipv6_address(value: Any) -> None: ...
def validate_ipv46_address(value: Any) -> None: ...
