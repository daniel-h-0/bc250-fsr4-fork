#!/usr/bin/env python3
from __future__ import annotations
import html, importlib.util, json, os, re, secrets, signal, socket, subprocess, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOME=Path.home(); BASE_PATH=HOME/'.local/bin/bc250-relay-v3'; CFG_PATH=HOME/'.config/bc250-relay-v3/config.json'
DATA=HOME/'.local/share/bc250-relay-v3'; CONTROL=DATA/'control-state.json'; HISTORY=DATA/'history.jsonl'
PROTOCOL='BC250_RELAY_V3'; VERSION='3.1'; SESSIONS=('fsr4','ps5','vcn')
LABELS={'fsr4':'FSR4 Main','ps5':'BC-250 Emulation Update','vcn':'VCN'}

spec=importlib.util.spec_from_file_location('bc250_relay_v3_base',BASE_PATH)
if not spec or not spec.loader: raise SystemExit(f'cannot load {BASE_PATH}')
base=importlib.util.module_from_spec(spec); spec.loader.exec_module(base)

def readj(p,default=None):
    try:return json.loads(Path(p).read_text())
    except Exception:return default

def writej(p,obj):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n'); os.chmod(t,0o600); t.replace(p)

def session_of(job,name=''):
    s=str(job.get('session') or '').strip().lower(); aliases={'fsr4-main':'fsr4','sharpemu':'ps5','emulation':'ps5','bc250-emulation':'ps5'}; s=aliases.get(s,s)
    if not s and '--' in name:s=name.split('--',1)[0]
    if not s:
        probe=' '.join(str(job.get(k,'')) for k in ('cwd','path','command')).lower()
        s='ps5' if ('sharpemu' in probe or 'ppsa' in probe) else 'vcn' if ('bc250-smu-unlock' in probe or 'vcn' in probe) else 'fsr4'
    if s not in SESSIONS: raise ValueError(f'invalid session {s!r}')
    return s

def history_add(x):
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    with HISTORY.open('a',encoding='utf-8') as f:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    try:
        ls=HISTORY.read_text().splitlines()
        if len(ls)>300:HISTORY.write_text('\n'.join(ls[-200:])+'\n')
    except Exception:pass

def history_get(n=24):
    try:ls=HISTORY.read_text().splitlines()[-n:]
    except Exception:return []
    out=[]
    for x in reversed(ls):
        try:out.append(json.loads(x))
        except Exception:pass
    return out

class Sessions:
    def __init__(self):
        raw=readj(CONTROL,{}) or {}; self.lock=threading.Lock(); self.d={}
        for s in SESSIONS:
            x=raw.get(s,{})
            self.d[s]={'mode':x.get('mode','running'),'priority':x.get('priority',''),'messages':list(x.get('messages',[]))[-30:],'updated':int(x.get('updated',time.time()))}
    def snap(self,s=None):
        with self.lock:return json.loads(json.dumps(self.d[s] if s else self.d))
    def save(self):writej(CONTROL,self.snap())
    def apply(self,s,action,text='',source='dashboard'):
        s=session_of({'session':s}); action=str(action).upper(); now=int(time.time())
        with self.lock:
            x=self.d[s]
            if action=='PAUSE':x['mode']='paused'
            elif action=='RESUME':x['mode']='running'
            elif action=='STOP':x['mode']='stopped'
            elif action=='NOTE':
                if not text.strip():raise ValueError('NOTE requires text')
            elif action=='PRIORITY':
                if not text.strip():raise ValueError('PRIORITY requires text')
                x['priority']=text.strip()
            elif action=='CLEAR_PRIORITY':x['priority']=''
            else:raise ValueError(f'unsupported action {action}')
            msg={'id':f'{s}-{now}-{secrets.token_hex(3)}','action':action,'text':text.strip(),'source':source,'unix':now}; x['messages'].append(msg); x['messages']=x['messages'][-30:]; x['updated']=now
        self.save(); return msg
    def allowed(self,s):
        m=self.snap(s)['mode']; return (m=='running','session-'+m if m!='running' else '')

class Runner:
    def __init__(self,cfg,sessions):self.cfg=cfg; self.sessions=sessions; self.delegate=base.Runner(cfg); self.active={}; self.lock=threading.Lock()
    def snapshot(self):
        now=time.monotonic()
        with self.lock:return [{'job_id':j,'session':x['session'],'started_unix':x['unix'],'elapsed_s':round(now-x['mono'],1),'command':x.get('command','')[:180]} for j,x in self.active.items()]
    def busy(self,s):
        with self.lock:return any(x['session']==s for x in self.active.values())
    def shell(self,job):
        s=session_of(job); job['session']=s; cwd=base.ensure_allowed(job['cwd'],self.cfg,exists=True,directory=True); cmd=str(job['command']); timeout=max(1,min(int(job.get('timeout',120)),int(self.cfg.get('max_timeout',1800))))
        env=os.environ.copy(); env.update({str(k):str(v) for k,v in (job.get('env') or {}).items()}); start=time.monotonic(); p=subprocess.Popen([self.cfg.get('shell','/bin/bash'),'-lc',cmd],cwd=str(cwd),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True); jid=str(job['job_id'])
        with self.lock:self.active[jid]={'p':p,'session':s,'mono':start,'unix':int(time.time()),'command':cmd}
        try:out,err=p.communicate(timeout=timeout); timed=False
        except subprocess.TimeoutExpired:
            timed=True
            try:os.killpg(p.pid,signal.SIGTERM)
            except Exception:pass
            try:out,err=p.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                try:os.killpg(p.pid,signal.SIGKILL)
                except Exception:pass
                out,err=p.communicate()
        finally:
            with self.lock:self.active.pop(jid,None)
        out=out or ''; err=err or ''; fo=base.save_artifact(jid,'stdout.log',out); fe=base.save_artifact(jid,'stderr.log',err); co,t1=base.clip(out); ce,t2=base.clip(err)
        return {'exit_code':124 if timed else p.returncode,'timed_out':timed,'duration_s':round(time.monotonic()-start,3),'cwd':str(cwd),'stdout':co,'stderr':ce,'stdout_truncated':t1,'stderr_truncated':t2,'stdout_artifact':fo,'stderr_artifact':fe}
    def cancel(self,jid):
        with self.lock:x=self.active.get(jid)
        if not x:return {'cancelled':False,'reason':'not-running'}
        try:os.killpg(x['p'].pid,signal.SIGTERM); return {'cancelled':True,'session':x['session']}
        except Exception as e:return {'cancelled':False,'reason':str(e)}
    def cancel_session(self,s):
        s=session_of({'session':s})
        with self.lock:ids=[j for j,x in self.active.items() if x['session']==s]
        return {j:self.cancel(j) for j in ids}
    def execute(self,job):
        s=session_of(job); job['session']=s; op=job.get('op')
        if op=='shell':return self.shell(job)
        if op=='cancel':return self.cancel(str(job['target_job_id']))
        if op=='control_status':return self.sessions.snap(s)
        if op=='ping':return {'pong':True,'host':socket.gethostname(),'time':int(time.time()),'session':s}
        return self.delegate.execute(job)

class Transport:
    def __init__(self,cfg,runner,sessions):
        self.cfg=cfg; self.runner=runner; self.sessions=sessions; self.repo=cfg['repo']; self.prefix=cfg.get('queue_prefix','relay-v3'); self.lock=threading.Lock(); self.inflight=set(); self.claimed=set(); self.last_ok=0; self.last_poll=0; self.last_error=''
    def ok(self):self.last_ok=int(time.time()); self.last_error=''
    def tstatus(self):
        now=int(time.time()); return {'last_ok_unix':self.last_ok,'last_ok_age_s':None if not self.last_ok else now-self.last_ok,'last_poll_unix':self.last_poll,'last_error':self.last_error}
    def hello(self):
        base.gh_put(self.repo,f'{self.prefix}/status/hello.json',{'protocol':PROTOCOL,'relay_version':VERSION,'token':self.cfg['token'],'host':socket.gethostname(),'allowed_roots':self.cfg['allowed_roots'],'http_port':self.cfg.get('http_port',8765),'sessions':list(SESSIONS),'job_filename':'<session>--<job_id>.json','result_filename':'<session>--<job_id>.json','version':3.1},'relay v3.1 hello'); self.ok()
    def status(self):
        x={'protocol':PROTOCOL,'relay_version':VERSION,'host':socket.gethostname(),'unix':int(time.time()),'active_jobs':self.runner.snapshot(),'sessions':self.sessions.snap(),'transport':self.tstatus()}; base.gh_put(self.repo,f'{self.prefix}/status/heartbeat.json',x,'relay v3.1 heartbeat'); base.gh_put(self.repo,f'{self.prefix}/status/sessions.json',{'protocol':PROTOCOL,'relay_version':VERSION,'unix':int(time.time()),'sessions':self.sessions.snap()},'relay v3.1 sessions'); self.ok()
    def result_path(self,s,j):return f'{self.prefix}/results/{s}--{j}.json'
    def worker(self,item,name,raw):
        s='fsr4'; jid=name[:-5]; start=time.time(); result=None
        try:
            job=json.loads(raw)
            if job.get('protocol')!=PROTOCOL:raise ValueError('wrong protocol')
            if not secrets.compare_digest(str(job.get('token','')),str(self.cfg['token'])):raise PermissionError('token mismatch')
            s=session_of(job,name); job['session']=s; jid=str(job.get('job_id') or jid)
            if '--' in name and not name.startswith(s+'--'):raise ValueError('filename/session mismatch')
            allowed,reason=self.sessions.allowed(s)
            result={'protocol':PROTOCOL,'relay_version':VERSION,'job_id':jid,'session':s,'host':socket.gethostname(),'control':self.sessions.snap(s)}
            if not allowed:result.update(status='blocked',error=reason,result={})
            else:result.update(status='ok',result=self.runner.execute(job))
        except Exception as e:result={'protocol':PROTOCOL,'relay_version':VERSION,'job_id':jid,'session':s,'status':'error','host':socket.gethostname(),'error':f'{type(e).__name__}: {e}','result':{},'control':self.sessions.snap(s)}
        try:
            base.gh_put(self.repo,self.result_path(s,jid),result,f'relay {s} result {jid}')
            if '--' not in name:base.gh_put(self.repo,f'{self.prefix}/results/{jid}.json',result,f'relay legacy result {jid}')
            try:base.gh_delete(self.repo,f'{self.prefix}/jobs/{name}',str(item.get('sha','')),f'relay consumed {s} {jid}')
            except Exception:pass
            self.ok()
        except Exception as e:self.last_error=f'publish: {type(e).__name__}: {e}'
        finally:
            history_add({'unix':int(time.time()),'session':s,'job_id':jid,'status':(result or {}).get('status','error'),'duration_s':round(time.time()-start,2)})
            with self.lock:self.inflight.discard(name); self.claimed.discard(s)
    def controls(self):
        for item in base.gh_list(self.repo,f'{self.prefix}/control'):
            name=item.get('name','')
            if not name.endswith('.json'):continue
            raw=base.gh_get_text(self.repo,f'{self.prefix}/control/{name}')
            if not raw:continue
            try:
                x=json.loads(raw)
                if x.get('protocol')!=PROTOCOL or not secrets.compare_digest(str(x.get('token','')),str(self.cfg['token'])):raise PermissionError('bad control auth')
                s=session_of(x,name); action=str(x.get('action','')).upper(); msg=self.sessions.apply(s,action,str(x.get('text','')),source='github-control')
                if action=='STOP':self.runner.cancel_session(s)
                base.gh_put(self.repo,f'{self.prefix}/control-results/{name}',{'protocol':PROTOCOL,'relay_version':VERSION,'status':'ok','session':s,'control':msg,'state':self.sessions.snap(s)},f'relay control {s} {action}')
                try:base.gh_delete(self.repo,f'{self.prefix}/control/{name}',str(item.get('sha','')),f'relay consumed control {s}')
                except Exception:pass
                self.ok()
            except Exception as e:self.last_error=f'control: {type(e).__name__}: {e}'
    def jobs(self):
        items=base.gh_list(self.repo,f'{self.prefix}/jobs'); self.last_poll=int(time.time()); self.ok()
        for item in items:
            name=item.get('name','')
            if not name.endswith('.json'):continue
            with self.lock:
                if name in self.inflight:continue
            raw=base.gh_get_text(self.repo,f'{self.prefix}/jobs/{name}')
            if not raw:continue
            try:s=session_of(json.loads(raw),name)
            except Exception:s='fsr4'
            with self.lock:
                if s in self.claimed:continue
                self.inflight.add(name); self.claimed.add(s)
            threading.Thread(target=self.worker,args=(item,name,raw),daemon=True,name=f'relay-{s}').start()
    def loop(self):
        self.hello(); hb=0
        while True:
            try:
                self.controls(); self.jobs(); now=time.time()
                if now-hb>30:self.status(); hb=now
            except Exception as e:self.last_error=f'{type(e).__name__}: {e}'
            time.sleep(float(self.cfg.get('poll_seconds',5)))

def dashboard(server,key):
    states=server.sessions.snap(); active={x['session']:x for x in server.runner.snapshot()}; cards=[]
    for s in SESSIONS:
        st=states[s]; a=active.get(s); status='RUNNING JOB' if a else st['mode'].upper(); detail=(a or {}).get('command') or st.get('priority') or 'No active job'
        cards.append(f'''<section class="card"><h2>{html.escape(LABELS[s])}</h2><b>{html.escape(status)}</b><p>{html.escape(detail)}</p><button onclick="c('{s}','PAUSE')">Pause</button><button onclick="c('{s}','RESUME')">Resume</button><button onclick="c('{s}','STOP')">Stop</button><p><input id="i-{s}" placeholder="note / priority"><button onclick="t('{s}','NOTE')">Note</button><button onclick="t('{s}','PRIORITY')">Priority</button></p></section>''')
    rows=''.join(f"<tr><td>{html.escape(str(x.get('session','')))}</td><td>{html.escape(str(x.get('job_id','')))}</td><td>{html.escape(str(x.get('status','')))}</td><td>{x.get('duration_s','')}</td></tr>" for x in history_get())
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BC-250 Relay</title><style>body{{font-family:system-ui;background:#111;color:#eee;max-width:1100px;margin:auto;padding:20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}.card{{background:#1b1b1b;border:1px solid #333;border-radius:14px;padding:16px}}button,input{{font:inherit;background:#222;color:#eee;border:1px solid #555;border-radius:8px;padding:8px;margin:2px}}input{{width:58%}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #333;text-align:left}}</style></head><body><h1>BC-250 Relay v3.1</h1><p>{html.escape(socket.gethostname())} · {len(active)} active job(s)</p><div class="grid">{''.join(cards)}</div><h2>Recent jobs</h2><table><tr><th>Session</th><th>Job</th><th>Status</th><th>s</th></tr>{rows}</table><script>const k={json.dumps(key)};async function c(s,a,x=''){{await fetch('/control',{{method:'POST',headers:{{'Content-Type':'application/json','X-Dashboard-Key':k}},body:JSON.stringify({{session:s,action:a,text:x}})}});setTimeout(()=>location.reload(),350)}}function t(s,a){{let e=document.getElementById('i-'+s);if(e.value.trim())c(s,a,e.value.trim())}}</script></body></html>'''.encode()

class Handler(BaseHTTPRequestHandler):
    def sendj(self,n,x):
        b=json.dumps(x,ensure_ascii=False).encode(); self.send_response(n); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def token(self):return secrets.compare_digest(self.headers.get('X-Relay-Token',''),self.server.cfg['token'])
    def dkey(self):
        k=self.headers.get('X-Dashboard-Key','')
        if not k:k=(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('key') or [''])[0]
        return secrets.compare_digest(k,self.server.cfg['dashboard_key'])
    def do_GET(self):
        p=urllib.parse.urlparse(self.path)
        if p.path=='/health':
            t=self.server.transport.tstatus(); ok=t['last_ok_age_s'] is None or t['last_ok_age_s']<180; return self.sendj(200 if ok else 503,{'ok':ok,'protocol':PROTOCOL,'relay_version':VERSION,'host':socket.gethostname(),'active':self.server.runner.snapshot(),'sessions':self.server.sessions.snap(),'transport':t})
        if p.path=='/':
            if not self.dkey():return self.sendj(401,{'error':'dashboard key required'})
            b=dashboard(self.server,self.server.cfg['dashboard_key']); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); return self.wfile.write(b)
        if not self.token():return self.sendj(401,{'error':'unauthorized'})
        if p.path=='/status':return self.sendj(200,{'protocol':PROTOCOL,'relay_version':VERSION,'active':self.server.runner.snapshot(),'sessions':self.server.sessions.snap(),'history':history_get(30)})
        return self.sendj(404,{'error':'not found'})
    def do_POST(self):
        p=urllib.parse.urlparse(self.path); n=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(min(n,2_000_000))
        if p.path=='/control':
            if not (self.dkey() or self.token()):return self.sendj(401,{'error':'unauthorized'})
            try:
                x=json.loads(raw); s=session_of(x); a=str(x.get('action','')).upper(); m=self.server.sessions.apply(s,a,str(x.get('text','')),source='dashboard'); cancelled=self.server.runner.cancel_session(s) if a=='STOP' else None; return self.sendj(200,{'status':'ok','control':m,'cancelled':cancelled,'state':self.server.sessions.snap(s)})
            except Exception as e:return self.sendj(400,{'status':'error','error':f'{type(e).__name__}: {e}'})
        if not self.token():return self.sendj(401,{'error':'unauthorized'})
        try:
            job=json.loads(raw); job.setdefault('job_id',f'http-{int(time.time()*1000)}'); job['session']=session_of(job); allowed,reason=self.server.sessions.allowed(job['session'])
            if not allowed and job.get('op') not in ('ping','control_status','cancel'):return self.sendj(409,{'job_id':job['job_id'],'session':job['session'],'status':'blocked','error':reason,'control':self.server.sessions.snap(job['session'])})
            return self.sendj(200,{'job_id':job['job_id'],'session':job['session'],'status':'ok','result':self.server.runner.execute(job),'control':self.server.sessions.snap(job['session'])})
        except Exception as e:return self.sendj(400,{'status':'error','error':f'{type(e).__name__}: {e}'})
    def log_message(self,*a):pass

def main():
    cfg=readj(CFG_PATH)
    if not cfg:raise SystemExit(f'missing {CFG_PATH}')
    if not cfg.get('dashboard_key'):cfg['dashboard_key']=secrets.token_urlsafe(18); writej(CFG_PATH,cfg)
    cfg['poll_seconds']=min(int(cfg.get('poll_seconds',5)),5)
    sessions=Sessions(); runner=Runner(cfg,sessions); transport=Transport(cfg,runner,sessions); http=ThreadingHTTPServer((cfg.get('http_host','127.0.0.1'),int(cfg.get('http_port',8765))),Handler); http.cfg=cfg; http.runner=runner; http.sessions=sessions; http.transport=transport; threading.Thread(target=http.serve_forever,daemon=True).start(); transport.loop()

if __name__=='__main__':main()
