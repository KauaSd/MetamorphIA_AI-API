"""Cache em memória com expiração por TTL e limite máximo de itens."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
import time
from typing import Generic, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedTTLCache(Generic[K, V]):
    """Dicionário thread-safe com TTL e remoção LRU quando atinge max_items."""

    def __init__(self, ttl_seconds: float = 3600.0, max_items: int = 200) -> None:
        self._ttl = float(ttl_seconds)
        self._max_items = max(1, int(max_items))
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = Lock()

    def _limpar_expirados(self, agora: float) -> None:
        chaves_exp = [k for k, (exp, _) in self._data.items() if exp <= agora]
        for k in chaves_exp:
            self._data.pop(k, None)

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        agora = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            exp, valor = item
            if exp <= agora:
                self._data.pop(key, None)
                return default
            self._data.move_to_end(key)
            return valor

    def __setitem__(self, key: K, value: V) -> None:
        agora = time.monotonic()
        with self._lock:
            self._limpar_expirados(agora)
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (agora + self._ttl, value)
            while len(self._data) > self._max_items:
                self._data.popitem(last=False)

    def pop(self, key: K, default: Optional[V] = None) -> Optional[V]:
        with self._lock:
            item = self._data.pop(key, None)
            if item is None:
                return default
            exp, valor = item
            if exp <= time.monotonic():
                return default
            return valor
