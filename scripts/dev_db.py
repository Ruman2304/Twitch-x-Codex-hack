"""Start an isolated development PostgreSQL cluster; never alters a system DB."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.local'/'postgres'
DATA=BASE/'data'
SOCKET=BASE/'socket'
PORT='55439'

def binary(name):
    candidates=[]
    if os.getenv('POSTGRES_BIN'):candidates.append(Path(os.environ['POSTGRES_BIN'])/name)
    discovered=shutil.which(name)
    if discovered:candidates.append(Path(discovered))
    candidates.extend([Path('/Library/PostgreSQL/18/bin')/name,Path('/opt/homebrew/opt/postgresql@18/bin')/name])
    for path in candidates:
        if path.is_file() and os.access(path,os.X_OK):return str(path)
    raise RuntimeError('PostgreSQL binaries not found. Install PostgreSQL or set POSTGRES_BIN to its bin directory.')

def run(name,*args,check=True,quiet=False):
    return subprocess.run([binary(name),*map(str,args)],check=check,
                          stdout=subprocess.DEVNULL if quiet else None,stderr=subprocess.DEVNULL if quiet else None)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','stop','status'])
    action=parser.parse_args().action
    try:
        if action=='stop':
            if DATA.exists():run('pg_ctl','-D',DATA,'-m','fast','-w','stop')
            return 0
        if action=='status':
            return run('pg_ctl','-D',DATA,'status',check=False).returncode
        if len(str(SOCKET).encode())>85:
            raise RuntimeError('Checkout path is too long for a PostgreSQL Unix socket. Use a shorter checkout or Docker.')
        BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
        BASE.chmod(0o700)
        SOCKET.mkdir(exist_ok=True,mode=0o700)
        if not (DATA/'PG_VERSION').exists():
            run('initdb','-D',DATA,'--auth=trust','-U','dareloop','--no-locale','-E','UTF8')
        running=run('pg_ctl','-D',DATA,'status',check=False,quiet=True).returncode==0
        if not running:
            options=f"-k {shlex.quote(str(SOCKET))} -p {PORT} -c listen_addresses=''"
            run('pg_ctl','-D',DATA,'-l',BASE/'server.log','-o',options,'-w','start')
        result=subprocess.run([binary('psql'),'-h',str(SOCKET),'-p',PORT,'-U','dareloop','-d','postgres','-tAc',"SELECT 1 FROM pg_database WHERE datname='dareloop'"],check=True,capture_output=True,text=True)
        if result.stdout.strip()!='1':run('createdb','-h',SOCKET,'-p',PORT,'-U','dareloop','dareloop')
        print('\nDevelopment database ready. Add this to your ignored .env:')
        print(f'DATABASE_URL="host={SOCKET} port={PORT} user=dareloop dbname=dareloop"')
        print('Local Unix socket only; access is limited by the private directory. No TCP listener.')
        return 0
    except (RuntimeError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
