#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, json, os, secrets, signal, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

APP="bc250-relay-v3"
HOME=Path.home()
CFG_DIR=HOME/".config"/APP
DATA_DIR=HOME/".local/share"/APP
CFG=CFG_DIR/"config.json"
STATE=DATA_DIR/"state.json"
ART=DATA_DIR/"artifacts"
PROTOCOL="BC250_RELAY_V3"

def jread(p:Path, default=None):
    try: return json.loads(p.read_text())
    except Exception: return default

def jwrite(p:Path, obj, mode=0o600):
    p.parent.mkdir(parents=True, exist_ok=True)
    t=p.with_suffix(p.suffix+".tmp")
    t.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+"\n")
    os.chmod(t, mode); t.replace(p)

def sh(args, *, input_text=None, timeout=30):
    return subprocess.run(args, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)

def gh_put(repo:str, path:str, obj:Any, message:str):
    content=base64.b64encode((json.dumps(obj,ensure_ascii=False,indent=2)+"\n").encode()).decode()
    ep=f"repos/{repo}/contents/{path}"
    old=None
    p=sh(["gh","api",ep], timeout=20)
    if p.returncode==0:
        try: old=json.loads(p.stdout).get("sha")
        except Exception: pass
    args=["gh","api","--method","PUT",ep,"-f",f"message={message}","-f",f"content={content}"]
    if old: args += ["-f",f"sha={old}"]
    p=sh(args, timeout=40)
    if p.returncode: raise RuntimeError(p.stderr.strip() or f"PUT {path}")

def gh_delete(repo:str, path:str, sha:str, message:str):
    p=sh(["gh","api","--method","DELETE",f"repos/{repo}/contents/{path}","-f",f"message={message}","-f",f"sha={sha}"], timeout=40)
    if p.returncode: raise RuntimeError(p.stderr.strip() or f"DELETE {path}")

def gh_get_text(repo:str, path:str)->str|None:
    p=sh(["gh","api",f"repos/{repo}/contents/{path}","-H","Accept: application/vnd.github.raw"], timeout=30)
    return None if p.returncode else p.stdout

def gh_list(repo:str, path:str)->list[dict]:
    p=sh(["gh","api",f"repos/{repo}/contents/{path}?per_page=100"], timeout=30)
    if p.returncode: return []
    try:
        x=json.loads(p.stdout); return x if isinstance(x,list) else []
    except Exception: return []

def resolve(p:str)->Path: return Path(os.path.expandvars(os.path.expanduser(p))).resolve()

def ensure_allowed(p:str, cfg:dict, *, exists=False, directory=False)->Path:
    x=resolve(p); roots=[resolve(r) for r in cfg["allowed_roots"]]
    if not any(x==r or r in x.parents for r in roots): raise PermissionError(f"outside allowed_roots: {x}")
    if exists and not x.exists(): raise FileNotFoundError(str(x))
    if directory and x.exists() and not x.is_dir(): raise NotADirectoryError(str(x))
    return x

def clip(s:str, n=28000):
    if len(s)<=n: return s,False
    return s[:n//2]+"\n...[truncated; full output saved locally]...\n"+s[-n//2:],True

def save_artifact(job_id:str, name:str, data:str)->str:
    d=ART/job_id; d.mkdir(parents=True, exist_ok=True)
    p=d/name; p.write_text(data, errors="replace"); return str(p)

class Runner:
    def __init__(self,cfg): self.cfg=cfg; self.active={}; self.lock=threading.Lock()
    def run_shell(self,job):
        cwd=ensure_allowed(job["cwd"], self.cfg, exists=True, directory=True)
        cmd=str(job["command"]); timeout=max(1,min(int(job.get("timeout",120)), int(self.cfg.get("max_timeout",1800))))
        env=os.environ.copy()
        for k,v in (job.get("env") or {}).items(): env[str(k)]=str(v)
        start=time.monotonic()
        p=subprocess.Popen([self.cfg.get("shell","/bin/bash"),"-lc",cmd],cwd=str(cwd),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        jid=job["job_id"]
        with self.lock: self.active[jid]=p
        try: out,err=p.communicate(timeout=timeout); to=False
        except subprocess.TimeoutExpired:
            to=True
            try: os.killpg(p.pid, signal.SIGTERM)
            except Exception: pass
            try: out,err=p.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid, signal.SIGKILL)
                except Exception: pass
                out,err=p.communicate()
        finally:
            with self.lock: self.active.pop(jid,None)
        out=out or ""; err=err or ""
        full_out=save_artifact(jid,"stdout.log",out); full_err=save_artifact(jid,"stderr.log",err)
        cout,t1=clip(out); cerr,t2=clip(err)
        return {"exit_code":124 if to else p.returncode,"timed_out":to,"duration_s":round(time.monotonic()-start,3),"cwd":str(cwd),"stdout":cout,"stderr":cerr,"stdout_truncated":t1,"stderr_truncated":t2,"stdout_artifact":full_out,"stderr_artifact":full_err}
    def cancel(self,jid):
        with self.lock: p=self.active.get(jid)
        if not p: return {"cancelled":False,"reason":"not-running"}
        try: os.killpg(p.pid,signal.SIGTERM); return {"cancelled":True}
        except Exception as e: return {"cancelled":False,"reason":str(e)}
    def execute(self,job):
        op=job.get("op")
        if op=="ping": return {"pong":True,"host":socket.gethostname(),"time":int(time.time())}
        if op=="shell": return self.run_shell(job)
        if op=="cancel": return self.cancel(str(job["target_job_id"]))
        if op=="read_file":
            p=ensure_allowed(job["path"],self.cfg,exists=True); txt=p.read_text(errors="replace")
            a=max(1,int(job.get("start_line",1))); b=int(job.get("end_line",a+399)); ls=txt.splitlines(); b=min(len(ls),b)
            return {"path":str(p),"content":"\n".join(f"{i}: {ls[i-1]}" for i in range(a,b+1))}
        if op=="write_file":
            p=ensure_allowed(job["path"],self.cfg); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(str(job.get("content",""))); return {"path":str(p),"bytes":p.stat().st_size}
        if op=="git_status":
            cwd=ensure_allowed(job["cwd"],self.cfg,exists=True,directory=True); p=sh(["git","-C",str(cwd),"status","--short","--branch"],timeout=60); return {"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
        if op=="git_diff":
            cwd=ensure_allowed(job["cwd"],self.cfg,exists=True,directory=True); p=sh(["git","-C",str(cwd),"diff","--no-ext-diff","--unified=3"],timeout=60); return {"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
        if op=="list_files":
            root=ensure_allowed(job["path"],self.cfg,exists=True,directory=True); lim=min(int(job.get("max_entries",500)),3000); depth=min(int(job.get("max_depth",3)),8); out=[]; base=len(root.parts)
            for cur,dirs,files in os.walk(root):
                cp=Path(cur)
                if len(cp.parts)-base>=depth: dirs[:]=[]
                dirs[:]=[d for d in dirs if d not in {".git",".cache"}]
                for n in sorted(dirs): out.append(str((cp/n).relative_to(root))+"/")
                for n in sorted(files): out.append(str((cp/n).relative_to(root)))
                if len(out)>=lim: return {"entries":out[:lim],"truncated":True}
            return {"entries":out,"truncated":False}
        raise ValueError(f"unsupported op {op}")

class GitHubTransport:
    def __init__(self,cfg,runner): self.cfg=cfg; self.runner=runner; self.repo=cfg["repo"]; self.prefix=cfg.get("queue_prefix","relay-v3"); self.seen=set(jread(STATE,{}).get("seen",[]))
    def publish_hello(self):
        gh_put(self.repo,f"{self.prefix}/status/hello.json",{"protocol":PROTOCOL,"token":self.cfg["token"],"host":socket.gethostname(),"allowed_roots":self.cfg["allowed_roots"],"http_port":self.cfg.get("http_port",8765),"version":3},"relay v3 hello")
    def heartbeat(self):
        gh_put(self.repo,f"{self.prefix}/status/heartbeat.json",{"protocol":PROTOCOL,"host":socket.gethostname(),"unix":int(time.time()),"active_jobs":list(self.runner.active)},"relay v3 heartbeat")
    def loop(self):
        self.publish_hello(); hb=0
        while True:
            now=time.time()
            if now-hb>60:
                try:self.heartbeat()
                except Exception:pass
                hb=now
            for item in gh_list(self.repo,f"{self.prefix}/jobs"):
                name=item.get("name","")
                if not name.endswith(".json") or name in self.seen: continue
                raw=gh_get_text(self.repo,f"{self.prefix}/jobs/{name}")
                if not raw: continue
                try:
                    job=json.loads(raw)
                    if job.get("protocol")!=PROTOCOL: raise ValueError("wrong protocol")
                    if not secrets.compare_digest(str(job.get("token","")),str(self.cfg["token"])): raise PermissionError("token mismatch")
                    jid=str(job["job_id"]); result={"protocol":PROTOCOL,"job_id":jid,"status":"ok","host":socket.gethostname(),"result":self.runner.execute(job)}
                except Exception as e:
                    jid=name[:-5]; result={"protocol":PROTOCOL,"job_id":jid,"status":"error","host":socket.gethostname(),"error":f"{type(e).__name__}: {e}","result":{}}
                try:
                    gh_put(self.repo,f"{self.prefix}/results/{jid}.json",result,f"relay result {jid}")
                    try: gh_delete(self.repo,f"{self.prefix}/jobs/{name}",str(item.get("sha","")),f"relay consumed {jid}")
                    except Exception: pass
                    self.seen.add(name); jwrite(STATE,{"seen":sorted(self.seen)[-2000:]})
                except Exception: pass
            time.sleep(float(self.cfg.get("poll_seconds",8)))

class Handler(BaseHTTPRequestHandler):
    server_version="BC250RelayV3/3"
    def _auth(self): return secrets.compare_digest(self.headers.get("X-Relay-Token",""), self.server.cfg["token"])
    def _json(self,code,obj):
        b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path=="/health": return self._json(200,{"ok":True,"protocol":PROTOCOL,"host":socket.gethostname(),"active":list(self.server.runner.active)})
        if not self._auth(): return self._json(401,{"error":"unauthorized"})
        if self.path.startswith("/artifact/"):
            rel=self.path[len("/artifact/"):]; p=(ART/rel).resolve()
            if ART.resolve() not in p.parents: return self._json(403,{"error":"bad path"})
            if not p.is_file(): return self._json(404,{"error":"not found"})
            data=p.read_bytes(); self.send_response(200); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        return self._json(404,{"error":"not found"})
    def do_POST(self):
        if not self._auth(): return self._json(401,{"error":"unauthorized"})
        n=int(self.headers.get("Content-Length","0")); raw=self.rfile.read(min(n,2_000_000))
        try: job=json.loads(raw); job.setdefault("protocol",PROTOCOL); job.setdefault("job_id",f"http-{int(time.time()*1000)}"); r=self.server.runner.execute(job); return self._json(200,{"job_id":job["job_id"],"status":"ok","result":r})
        except Exception as e:return self._json(400,{"status":"error","error":f"{type(e).__name__}: {e}"})
    def log_message(self,*a): pass

def init(args):
    roots=[str(resolve(x)) for x in args.root]
    cfg={"protocol":PROTOCOL,"repo":args.repo,"queue_prefix":"relay-v3","token":secrets.token_urlsafe(32),"allowed_roots":roots,"poll_seconds":8,"shell":"/bin/bash","max_timeout":1800,"http_host":"127.0.0.1","http_port":8765}
    jwrite(CFG,cfg); print(CFG)

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("init"); p.add_argument("--repo",default="dmorazasanchez/hola"); p.add_argument("--root",action="append",required=True); sub.add_parser("run"); a=ap.parse_args()
    if a.cmd=="init": return init(a)
    cfg=jread(CFG)
    if not cfg: raise SystemExit(f"missing {CFG}")
    runner=Runner(cfg); httpd=ThreadingHTTPServer((cfg.get("http_host","127.0.0.1"),int(cfg.get("http_port",8765))),Handler); httpd.cfg=cfg; httpd.runner=runner
    threading.Thread(target=httpd.serve_forever,daemon=True).start(); GitHubTransport(cfg,runner).loop()

if __name__=="__main__": main()
