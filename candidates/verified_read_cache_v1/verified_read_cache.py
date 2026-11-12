"""One-entry cache of verified journal bytes, never based only on timestamps."""
import copy
import hashlib

def install(core):
    if getattr(core.Store, '_verified_bytes_cache', False):
        return
    original = core.Store.read
    cache = {}
    def read(self):
        path = self.journal
        if not path.exists():
            cache.clear()
            return original(self)
        raw = path.read_bytes()
        key = (str(path.resolve()), len(raw), hashlib.sha256(raw).digest())
        if cache.get('key') == key:
            return copy.deepcopy(cache['state'])
        # Read/validate through the accepted implementation. Only cache when
        # its input remained byte-identical, including changes outside UGS locks.
        cache.clear()
        state = original(self)
        after = path.read_bytes()
        if raw == after:
            cache.update(key=key, state=copy.deepcopy(state))
        return state
    core.Store.read = read
    core.Store._verified_bytes_cache = True
