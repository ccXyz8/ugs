"""Native selective telemetry. Index is canonical; UUID is legacy metadata only."""
import csv
import io
import os
import re
import subprocess
import time

FIELDS = 'index,pci.bus_id,driver_version,memory.used,memory.reserved,memory.total,utilization.gpu,utilization.memory,mig.mode.current,mig.mode.pending,compute_mode,reset_status.reset_required,reset_status.drain_and_reset_recommended,ecc.errors.uncorrected.volatile.total'
BUS = re.compile(r'^GPU ([0-9A-Fa-f]{8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7])$')

def parse(csv_text, detail_text, names):
    detail = {}; current = None; process = None
    for line in detail_text.splitlines():
        match = BUS.fullmatch(line.strip()) if not line.startswith(' ') else None
        if match:
            bus = match[1].upper()
            if bus in detail: raise ValueError('Duplicate PCI identity')
            current = detail[bus] = {'processes': None, 'process_none':False, 'pending': None, 'failure': None}
            process = None
            continue
        if current is None: continue
        text = line.strip()
        if text == 'Processes':
            current['processes'] = []; process = None
        elif text.startswith('Processes') and ':' in text:
            if text.split(':',1)[1].strip() != 'None': raise ValueError('Unknown process visibility')
            current['processes'] = []; current['process_none']=True; process = None
        elif text.startswith('Process ID') and ':' in text:
            pid = text.split(':',1)[1].strip()
            if not pid.isdigit() or int(pid)<=0 or current['processes'] is None: raise ValueError('Invalid process PID')
            process = {'pid':pid,'type':None};current['processes'].append(process)
        elif text.startswith('Type') and ':' in text:
            if process is None: raise ValueError('Process type without identity')
            value=text.split(':',1)[1].strip()
            if value not in ('C','G','C+G','M'):raise ValueError('Unknown process type')
            process['type']=value
        elif text.startswith('Pending') and ':' in text:
            current['pending']=text.split(':',1)[1].strip()
        elif text.startswith('Remapping Failure Occurred') and ':' in text:
            current['failure']=text.split(':',1)[1].strip()
    cards=[];indices=set();uuids=set();buses=set()
    for row in csv.reader(io.StringIO(csv_text)):
        row=[x.strip() for x in row]
        if len(row)!=14:raise ValueError('Incomplete GPU fields')
        row.insert(2,row[0])
        index,bus,uuid,driver=row[:4];bus=bus.upper()
        if not index.isdigit() or not 0<=int(index)<8 or int(index) in indices or bus in buses or uuid in uuids or not driver:raise ValueError('Invalid GPU inventory')
        indices.add(int(index));buses.add(bus);uuids.add(uuid)
        if any(not x.isdigit() for x in row[4:9]+row[14:]):raise ValueError('Unknown numeric telemetry')
        d=detail.get(bus)
        if not d or d['processes'] is None or (not d['processes'] and not d['process_none']) or any(not p['type'] for p in d['processes']):raise ValueError('Incomplete process visibility')
        if d['pending'] not in ('Yes','No') or d['failure'] not in ('Yes','No'):raise ValueError('Unknown remapping health')
        name=names.get(uuid)
        if not name or 'A100' not in name:raise ValueError('Unreviewed GPU type')
        cards.append(dict(index=int(index),gpu_index=int(index),pci_bus_id=bus,uuid=index,name=name,driver_version=driver,memory_mib=int(row[4]),memory_reserved_mib=int(row[5]),memory_total_mib=int(row[6]),util=int(row[7]),memory_util=int(row[8]),health_ok=row[9:14]==['Disabled','Disabled','Default','No','No'] and d['pending']=='No' and d['failure']=='No',ecc_uncorrectable=int(row[14]),processes=d['processes']))
    if indices!=set(range(8)) or set(detail)!=buses:raise ValueError('Expected exact eight GPU inventory')
    return sorted(cards,key=lambda x:x['index'])

class Scanner:
    def __init__(self):self.names={};self.driver=None;self.last_timings={}
    def __call__(self):
        start=time.monotonic();timings={}
        def run(label,args):
            t=time.monotonic()
            try:return subprocess.check_output(['nvidia-smi',*args],text=True,timeout=12,env={**os.environ,'LC_ALL':'C'})
            except (OSError,subprocess.SubprocessError) as exc:
                raise ValueError('GPU_TELEMETRY_UNAVAILABLE:'+label+':'+type(exc).__name__) from exc
            finally:timings[label]=time.monotonic()-t
        try:
            base=run('dynamic_fields',['--query-gpu='+FIELDS,'--format=csv,noheader,nounits'])
            rows=list(csv.reader(io.StringIO(base)));identities={(x[0].strip(),x[1].strip(),x[2].strip()) for x in rows if len(x)==14}
            if identities!=self.driver:
                raw=run('device_names',['--query-gpu=index,name','--format=csv,noheader,nounits'])
                names={x[0].strip():x[1].strip() for x in csv.reader(io.StringIO(raw)) if len(x)==2}
            else:names=self.names
            detail=run('processes_and_remapping',['-q','-d','PIDS,ROW_REMAPPER'])
            result=parse(base,detail,names)
            self.names=names;self.driver=identities
            return result
        finally:
            timings['total']=time.monotonic()-start;self.last_timings=timings
