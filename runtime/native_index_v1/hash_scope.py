"""Bounded content-exact SHA reuse within an explicit validation scope."""
import contextlib
import contextvars
import hashlib
from pathlib import Path
MAX_FILE=1024*1024
MAX_CACHE=8*1024*1024

def install(core):
    if getattr(core,'_hash_scope_v1',False):return
    original=core.hash_file
    active=contextvars.ContextVar('ugs_hash_scope',default=None)
    stats={'hashed':0,'reused':0}
    def hash_file(name):
        cache=active.get()
        if cache is None:return original(name)
        path=Path(name).resolve(strict=True)
        # NFS timestamps may not distinguish same-size edits. Read actual bytes;
        # never trust mtime/ctime/inode alone for source integrity.
        with path.open('rb') as stream:raw=stream.read(MAX_FILE+1)
        if len(raw)>MAX_FILE:return original(path)
        key=str(path);prior=cache['files'].get(key)
        if prior is not None and prior[0]==raw:
            stats['reused']+=1
            return prior[1]
        result=hashlib.sha256(raw).hexdigest();stats['hashed']+=1
        size=cache['bytes']-(len(prior[0]) if prior else 0)+len(raw)
        if size>MAX_CACHE:
            cache['files'].clear();size=len(raw)
        cache['files'][key]=(raw,result);cache['bytes']=size
        return result
    @contextlib.contextmanager
    def scope():
        if active.get() is not None:
            yield
            return
        token=active.set({'files':{},'bytes':0})
        try:yield
        finally:active.reset(token)
    core.hash_file=hash_file;core.hash_scope=scope;core.hash_scope_stats=stats;core._hash_scope_v1=True
