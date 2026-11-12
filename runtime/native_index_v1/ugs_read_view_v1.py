"""Verified, query-local snapshots. No cache is used by dispatch or mutation paths."""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import fcntl

COMMANDS = {('experiment', 'list'), ('scheduler', 'status'), ('scheduler', 'get')}
CACHE_NAME = 'read-view-prefix-v1.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(line, expected=None):
    if not line.endswith(b'\n'):
        raise ValueError('Incomplete journal tail; fail closed, do not remove it.')
    row = json.loads(line)
    state = row['state']
    if digest(canonical(state)) != row['sha256'] or (expected is not None and state['revision'] != expected):
        raise ValueError('Journal integrity error; query blocked')
    return state


def safe_cache(path):
    try:
        st = path.lstat()
        return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1 and not st.st_mode & 0o077
    except FileNotFoundError:
        return True
    except OSError:
        return False


def read_proof(path):
    try:
        if not safe_cache(path):return None
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(4097)
        if len(data) > 4096:return None
        envelope = json.loads(data)
        proof = envelope['proof']
        if envelope['sha256'] != digest(canonical(proof)) or proof['version'] != 1:return None
        if any(type(proof[k]) is not int for k in ('bytes', 'tail_start', 'revision')):return None
        if not 0 <= proof['tail_start'] < proof['bytes'] or proof['revision'] < 1:return None
        return proof
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_proof(path, raw, state):
    # Bounded derived acceleration metadata; never contains queue/job state.
    if not raw or not safe_cache(path):return
    proof = {'version': 1, 'bytes': len(raw), 'tail_start': raw.rfind(b'\n', 0, len(raw)-1)+1,
             'revision': state['revision'], 'prefix_sha256': digest(raw)}
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.read-view-', dir=str(path.parent))
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical({'proof': proof, 'sha256': digest(canonical(proof))}))
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp, path)
    except OSError:
        pass  # A cache write failure cannot invalidate an already verified query.
    finally:
        if temp:
            try:os.unlink(temp)
            except FileNotFoundError:pass


def replay(raw, initial, proof=None):
    """Rehash ALL cached prefix bytes; only JSON decoding is skipped on a hit."""
    state = initial()
    offset = 0
    if proof and proof['bytes'] <= len(raw):
        end = proof['bytes'];start = proof['tail_start']
        if raw[end-1:end] == b'\n' and (start == 0 or raw[start-1:start] == b'\n') and digest(raw[:end]) == proof['prefix_sha256']:
            try:
                cached = record(raw[start:end], proof['revision'])
            except (ValueError, KeyError, TypeError):
                pass
            else:
                state = cached;offset = end
    for line in raw[offset:].splitlines(keepends=True):
        state = record(line, state['revision']+1)
    return state


def snapshot(store, initial, use_cache=True):
    # Only the byte copy holds the shared lock. Validation happens on immutable bytes.
    with store.lock.open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:raw = store.journal.read_bytes()
        except FileNotFoundError:raw = b''
    cache = store.root / CACHE_NAME
    state = replay(raw, initial, read_proof(cache) if use_cache else None)
    if use_cache:write_proof(cache, raw, state)
    return state


@contextlib.contextmanager
def session(core, args, use_cache=True):
    if tuple(args[:2]) not in COMMANDS:
        yield
        return
    original = core.Store
    snapshots = {}
    class ReadOnlyStore(original):
        def read(self):
            key = str(self.root.resolve())
            if key not in snapshots:
                snapshots[key] = snapshot(self, core.initial, use_cache)
            return copy.deepcopy(snapshots[key])
        @contextlib.contextmanager
        def tx(self, event=None):
            if event is not None:
                raise ValueError('Read-only query cannot write a state transaction')
            yield self.read()
    core.Store = ReadOnlyStore
    try:yield
    finally:core.Store = original
