# pyright: reportExplicitAny=false
"""Type stubs for ``tortoise.backends.asyncpg.client``.

Partial stub: declares the ``AsyncpgDBClient`` surface used by
``tortoise_extended``, including the ``_tortoise_extended_codec_patched``
class attribute that the pgvector codec monkey-patch sets at import time.
"""

from typing import Any

from asyncpg import Pool

class AsyncpgDBClient:
    _tortoise_extended_codec_patched: bool

    async def create_pool(self, **kwargs: Any) -> Pool: ...
