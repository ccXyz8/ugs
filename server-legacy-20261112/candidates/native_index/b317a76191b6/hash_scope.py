"""Deduplicate identical file hashes only within one explicit validation scope."""
import contextlib
import contextvars
from pathlib import Path


def install(core):
    if getattr(core,'_hash_scope_v1',False):return
    original=core.hash_file
    active=contextvars.ContextVar('ugs_hash_scope',default=None)
    def fingerprint(path):
        s=path.stat()
        return (str(path),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    def hash_file(name):
        cache=active.get()
        if cache is None:return original(name)
        path=Path(name).resolve(strict=True);before=fingerprint(path)
        if before in cache:return cache[before]
        result=original(path)
        if fingerprint(path)!=before:raise ValueError('File changed during SHA verification')
        cache[before]=result
        return result
    @contextlib.contextmanager
    def scope():
        if active.get() is not None:
            yield
            return
        token=active.set({})
        try:yield
        finally:active.reset(token)
    core.hash_file=hash_file;core.hash_scope=scope;core._hash_scope_v1=True
