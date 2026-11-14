"""Explicit, hash-bound gpu07 storage recovery. No normal dispatch integration."""
import contextlib,fcntl,gzip,hashlib,json,os,sys,uuid
from pathlib import Path
EXPECTED={
 'events.jsonl':('71a62debb1a8a6ec422f48a6f230c9461b2143852093bd11b12cd43b61ed860c',47621611,12),
 'resources.jsonl':('c15df41be540867f0ecbc643a4a583f83bb32454dce77a1425342319a819559a',58071538,19278)}
def digest(data):return hashlib.sha256(data).hexdigest()
def validate_prefix(data,state_journal=False):
    if not data.endswith(b'\n'):raise ValueError('Incomplete prefix')
    last=None
    for count,line in enumerate(data.splitlines(),1):
        row=json.loads(line)
        if not isinstance(row,dict):raise ValueError('Non-object record')
        if state_journal:
            state=row['state']
            if state['revision']!=count or digest(json.dumps(state,sort_keys=True,separators=(',',':')).encode())!=row['sha256']:
                raise ValueError('State SHA/revision mismatch')
        last=row
    return count,last

def write_new(path,data):
    with path.open('xb') as f:
        os.chmod(path,0o600);f.write(data);f.flush();os.fsync(f.fileno())
    if path.read_bytes()!=data:raise IOError('Readback mismatch')
def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def main():
    assert os.uname().nodename.split('.')[0]=='gpu07' and os.getuid()==2087
    root=Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler')
    sys.path.insert(0,str(root/'runtime/931207b36c12'));import ugs_idle_v2_entry as entry
    c=entry.core
    with contextlib.ExitStack() as stack:
        for name in ('daemon.lock','maintenance.lock','state.lock','observation.lock'):
            f=stack.enter_context((root/name).open('a+b'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        raws={};prefixes={};details={}
        for name,(sha,end,rows) in EXPECTED.items():
            path=root/name
            assert not path.is_symlink()
            raw=path.read_bytes();assert digest(raw)==sha,'Original changed: '+name
            prefix=raw[:end];count,last=validate_prefix(prefix,name=='events.jsonl');assert count==rows
            if name=='events.jsonl':state=last['state']
            raws[name]=raw;prefixes[name]=prefix
            details[name]={'original_sha256':sha,'original_bytes':len(raw),'restored_bytes':end,'quarantined_bytes':len(raw)-end,'retained_records':count,'restored_sha256':digest(prefix)}
        assert not state['active_runs'] and not c.live(state['daemon']['identity']) and not state.get('recovery')
        assert not entry.cli.maintenance.status()['maintenance_alive']
        assert c.release_ok(state)
        job=c.get_job(state,'job-000110');assert job['status']=='QUEUED'
        folder=root/'acceptance'/('storage-recovery-20261114-'+uuid.uuid4().hex[:12]);folder.mkdir(mode=0o700)
        archives={}
        for name in ('events.jsonl','resources.jsonl','service-events.jsonl','service.log'):
            raw=raws.get(name)
            if raw is None:raw=(root/name).read_bytes()
            payload=gzip.compress(raw,mtime=0);assert len(payload)<16*1024*1024
            target=folder/(name+'.gz');write_new(target,payload)
            assert gzip.decompress(target.read_bytes())==raw
            archives[name]={'path':str(target),'original_sha256':digest(raw),'compressed_bytes':len(payload)}
        report={'at':c.stamp(),'details':details,'archives':archives,'job_id':'job-000110','job_status_before':'QUEUED','phase':'prepared','source_pins_unchanged':True}
        write_new(folder/'prepared.json',(json.dumps(report,indent=2)+'\n').encode());sync_dir(folder)
        for name,prefix in prefixes.items():
            temp=root/('.'+name+'.recovery-'+uuid.uuid4().hex);write_new(temp,prefix)
            os.replace(temp,root/name);sync_dir(root)
            assert (root/name).read_bytes()==prefix
        restored=c.Store().read();assert restored==state and c.release_ok(restored)
        report.update(phase='restored',completed_at=c.stamp(),jobs_unchanged=True)
        write_new(folder/'completed.json',(json.dumps(report,indent=2)+'\n').encode());sync_dir(folder)
        print(json.dumps(report));print('PROOF_SHA',digest((folder/'completed.json').read_bytes()))
if __name__=='__main__':main()
