"""Temporary user-requested scan. Uses the full pinned package, never mocks."""
import functools, hashlib, http.server, io, json, os, pathlib, platform, signal, subprocess, sys, tarfile, threading, time, traceback, urllib.request, zipfile
P = pathlib.Path('/tmp/svs-diagnostic'); P.mkdir(exist_ok=True)
PUB = P/'public'; PUB.mkdir(exist_ok=True)
SRC = P/'source'; SRC.mkdir(exist_ok=True)
REPORTS = PUB/'reports'; REPORTS.mkdir(exist_ok=True)
LOGS = PUB/'logs'; LOGS.mkdir(exist_ok=True)
PIN = 'c29824f809b5ed9f23f40b8b03898674fac580b8'
signal.alarm(1200)
(PUB/'status.json').write_text(json.dumps({'stage':'starting','pin':PIN}))
server = http.server.ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))), functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(PUB)))
threading.Thread(target=server.serve_forever,daemon=True).start()
def stage(s):
    (PUB/'status.json').write_text(json.dumps({'stage':s,'pin':PIN})); print('DIAGNOSTIC_STAGE',s,flush=True)
def fetch(url, path, expected=None):
    req=urllib.request.Request(url,headers={'User-Agent':'abicheck-svs-diagnostic'})
    with urllib.request.urlopen(req,timeout=90) as r: data=r.read()
    digest=hashlib.sha256(data).hexdigest()
    if expected and digest!=expected: raise RuntimeError('SHA256 mismatch '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    print('VERIFIED_FILE',path.name,len(data),digest,flush=True)
    return data,digest
def command(name,args,timeout=240,cwd=None):
    start=time.monotonic()
    with (LOGS/(name+'.stdout')).open('wb') as out,(LOGS/(name+'.stderr')).open('wb') as err:
        p=subprocess.Popen(args,stdout=out,stderr=err,cwd=cwd or P,start_new_session=True)
        try: rc=p.wait(timeout=timeout); expired=False
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL); rc=p.wait(); expired=True
    item={'name':name,'command':args,'exit_code':rc,'wall_seconds':round(time.monotonic()-start,3),'timed_out':expired}
    (LOGS/(name+'.run.json')).write_text(json.dumps(item,indent=2)); print('COMMAND_RESULT',json.dumps(item),flush=True)
    return item
def must(name,args,timeout=240,cwd=None):
    result=command(name,args,timeout,cwd)
    if result['exit_code']!=0: raise RuntimeError(name+' failed; inspect saved logs')
def unpack(data,dest):
    dest.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t: t.extractall(dest,filter='data')
results=[]; manifest={'abicheck_main_commit':PIN,'svs_head':'7058e9605a54180aa64fbb7a81a82aa47f07eeff','svs_merge':'cb055c456578cd27a9eb5955786115acc12986f5','python':sys.version,'platform':platform.platform()}
try:
    stage('download-verified-inputs')
    for variant,key,digest in [('default','SVS_DEFAULT_URL','9dd801f22b8540bba93e9c20bcf268563c17cdfc4861fc5db5f3b7655ec40086'),('public-only','SVS_PUBLIC_URL','39a3dbabfba4b82734101c435d5bf096c256e765aafab1db39da56545a14a1fe')]:
        data,h=fetch(os.environ[key],P/(variant+'.zip'),digest)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=[n for n in z.namelist() if n.endswith('.tar.gz')]
            if len(names)!=1: raise RuntimeError('Ambiguous artifact '+variant)
            unpack(z.read(names[0]),P/'inputs'/variant)
        manifest[variant+'_zip_sha256']=h
    data,h=fetch('https://github.com/intel/ScalableVectorSearch/releases/download/v0.4.0/svs-cpp-runtime-bindings.tar.gz',P/'baseline.tar.gz','90833ee345afc0b448d56f8f2db743a9d8cf707cc4879b2b1df6d73c0a150a68')
    unpack(data,P/'inputs/release'); manifest['baseline_archive_sha256']=h
    data,h=fetch('https://codeload.github.com/abicheck/abicheck/tar.gz/'+PIN,P/'source.tar.gz')
    unpack(data,SRC); source=SRC/('abicheck-'+PIN); manifest['source_archive_sha256']=h
    stage('install-full-package-and-compiler')
    must('pip-install',[sys.executable,'-m','pip','install',str(source),'pytest','pytest-cov','pytest-xdist','pytest-timeout','hypothesis','filelock','jsonschema'],300)
    must('apt-update',['apt-get','update','-qq'],120)
    must('apt-install',['apt-get','install','-y','--no-install-recommends','clang','g++','binutils'],300)
    must('clang-version',['clang++','--version']); must('gcc-version',['g++','--version']); must('pip-freeze',[sys.executable,'-m','pip','freeze'])
    import abicheck
    installed=pathlib.Path(abicheck.__file__).parent
    count=0
    for f in installed.rglob('*.py'):
        ref=source/'abicheck'/f.relative_to(installed)
        if not ref.exists() or ref.read_bytes()!=f.read_bytes(): raise RuntimeError('Installed module differs '+str(f))
        count+=1
    manifest['installed_python_modules_verified']=count
    (PUB/'manifest.json').write_text(json.dumps(manifest,indent=2))
    cfg=P/'compile.yml'; cfg.write_text('compile:\n  frontend: clang\n  standard: c++20\n  args: [\"-include\", \"cstddef\"]\n')
    suppress=P/'svs-suppressions.yml'
    fetch('https://raw.githubusercontent.com/intel/ScalableVectorSearch/7058e9605a54180aa64fbb7a81a82aa47f07eeff/.github/abi-suppressions.yml',suppress)
    (PUB/'compile.yml').write_bytes(cfg.read_bytes()); (PUB/'svs-suppressions.yml').write_bytes(suppress.read_bytes())
    command('compare-help',['abicheck','compare','--help'])
    def compare(name,old,new,headers=True,suppressed=False):
        a=P/'inputs'/old; b=P/'inputs'/new; out=REPORTS/(name+'.json')
        args=['abicheck','compare',str(a/'lib/libsvs_runtime.so'),str(b/'lib/libsvs_runtime.so'),'--policy','strict_abi','--version','old='+old,'--version','new='+new]
        if headers: args+=['--config',str(cfg),'--header','old='+str(a/'include/svs/runtime'),'--header','new='+str(b/'include/svs/runtime'),'--include','old='+str(a/'include'),'--include','new='+str(b/'include')]
        if suppressed: args+=['--suppress',str(suppress)]
        args+=['--format','json','--output',str(out),'--write','markdown='+str(out.with_suffix('.md')),'--write','html='+str(out.with_suffix('.html'))]
        item=command(name,args,180)
        if out.exists():
            doc=json.loads(out.read_text()); item['report_keys']=list(doc)
            for key in ['verdict','summary','analysis_assurance','confidence','evidence_tier','evidence_tiers','coverage_warnings']:
                if key in doc: item[key]=doc[key]
            changes=doc.get('changes',[]); kinds={}
            for c in changes:
                kind=c.get('kind',''); kinds[kind]=kinds.get(kind,0)+1
            item['change_kind_counts']=kinds
            item['notable_changes']=[c for c in changes if any(x in c.get('kind','') for x in ['vtable','experimental','removed','layout']) and ('svs' in str(c))]
            item['report_sha256']=hashlib.sha256(out.read_bytes()).hexdigest()
        results.append(item); (PUB/'results.json').write_text(json.dumps(results,indent=2)); print('SCAN_RESULT',json.dumps(item),flush=True)
    stage('full-cli-scans')
    for item in [('release-default-binary','release','default',False,False),('release-default','release','default',True,False),('release-default-suppressed','release','default',True,True),('self-default','default','default',True,False),('self-public-only','public-only','public-only',True,False),('public-to-default','public-only','default',True,False),('default-to-public','default','public-only',True,False)]: compare(*item)
    stage('complete')
except BaseException as exc:
    (PUB/'error.txt').write_text(traceback.format_exc()); print(traceback.format_exc(),flush=True); stage('failed')
finally:
    (PUB/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (PUB/'results.json').write_text(json.dumps(results,indent=2))
    with zipfile.ZipFile(PUB/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in PUB.rglob('*'):
            if f.is_file() and f.name!='evidence.zip': z.write(f,f.relative_to(PUB))
    print('EVIDENCE_READY',hashlib.sha256((PUB/'evidence.zip').read_bytes()).hexdigest(),flush=True)
    time.sleep(300)
    server.shutdown()
