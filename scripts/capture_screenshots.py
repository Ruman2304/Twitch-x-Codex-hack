"""Capture real DareLoop UI with isolated demo data (never the active workspace).
Requires websocket-client and Chrome/Chromium. Run from the repository root.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from apps.api.app import main
from apps.api.app.services.sessions import SessionService

EXAMPLES=[
    ('Draw a mascot in 60 seconds','Draw a friendly mascot using only three shapes. Show your sketch to chat when you are ready.','Creative',60),
    ('Narrate like a sports commentator','Describe your next minute of ordinary gameplay like a live sports final.','Gaming',60),
    ('Tell a story in five words','Make up a five-word story and let chat suggest a title.','Creative',90),
]

def capture(chrome, output):
    import websocket
    # A fresh memory demo: no real database, Twitch calls, accounts, cameras or OBS links.
    main.GAME=SessionService(demo=True);main.AUTH=None;main.CHAT=None;main.DEMO=True
    class QuietHandler(main.Handler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    output.mkdir(parents=True,exist_ok=True)
    browser=None;ws=None
    try:
        with tempfile.TemporaryDirectory(prefix='dareloop-docs-chrome-') as profile:
            browser=subprocess.Popen([chrome,'--headless','--no-first-run','--no-default-browser-check',
                '--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            portfile=Path(profile)/'DevToolsActivePort'
            deadline=time.monotonic()+15
            while not portfile.exists():
                if time.monotonic()>deadline or browser.poll() is not None:raise RuntimeError('Chrome could not start')
                time.sleep(.1)
            port=portfile.read_text().splitlines()[0]
            with urlopen('http://127.0.0.1:'+port+'/json') as response:
                target=next(t for t in json.load(response) if t['type']=='page')
            ws=websocket.create_connection(target['webSocketDebuggerUrl'],suppress_origin=True,timeout=15)
            counter=0
            def cdp(method,params=None):
                nonlocal counter
                counter+=1;identifier=counter
                ws.send(json.dumps(dict(id=identifier,method=method,params=params or {})))
                while True:
                    r=json.loads(ws.recv())
                    if r.get('id')==identifier:
                        if 'error' in r:raise RuntimeError(r['error'])
                        return r.get('result',{})
            def js(expression):
                result=cdp('Runtime.evaluate',dict(expression=expression,returnByValue=True,awaitPromise=True))
                if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
                return result.get('result',{}).get('value')
            def wait(expression):
                until=time.monotonic()+10
                while not js(expression):
                    if time.monotonic()>until:raise RuntimeError('Page did not settle: '+expression)
                    time.sleep(.1)
            def photo(name,overlay=False):
                js('document.fonts.ready')
                time.sleep(.15)
                encoded=cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))['data']
                (output/name).write_bytes(base64.b64decode(encoded))
                print('Captured',name)
            def sync():
                js('refresh()');wait('Boolean(ui.data)');time.sleep(.15)
            def scroll(selector):js(f'document.querySelector({json.dumps(selector)}).scrollIntoView({{block:"start",behavior:"instant"}})')
            cdp('Page.enable');cdp('Runtime.enable')
            cdp('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1000,deviceScaleFactor=1,mobile=False))
            base=f'http://127.0.0.1:{server.server_port}'
            cdp('Page.navigate',dict(url=base));wait('typeof ui!=="undefined"&&Boolean(ui.data)')
            scroll('#forge');photo('01-empty-forge.png')
            for title,instructions,category,duration in EXAMPLES:
                main.GAME.execute('/api/v1/challenges',dict(title=title,instructions=instructions,category=category,duration_sec=duration,safety_confirmed=True))
            sync();js('window.scrollTo(0,0)');photo('02-dashboard.png')
            js('document.querySelector("#add-challenge").click()')
            for key,value in [('custom-title','Invent a superhero catchphrase'),('custom-instructions','Create a funny superhero catchphrase, then perform it in your best announcer voice.'),('custom-category','Creative'),('custom-duration','60')]:
                js(f'document.getElementById({json.dumps(key)}).value={json.dumps(value)}')
            js('document.querySelector("#custom-safety").checked=true');photo('03-add-challenge.png');js('document.querySelector("#cancel-challenge").click()')
            for c in main.GAME.snapshot()['challenges']:main.GAME.execute('/api/v1/challenges/'+c['id']+'/approval',{'approval':'approved'})
            sync();scroll('#forge');photo('04-approved-forge.png')
            main.GAME.execute('/api/v1/rounds',{})
            for viewer,option in [('viewer-1',1),('viewer-2',1),('viewer-3',2)]:main.GAME.execute('/api/v1/demo/vote',{'viewer':viewer,'option':option})
            sync();scroll('#arena');photo('05-live-voting.png')
            main.GAME.execute('/api/v1/rounds/close',{});sync();scroll('#arena');photo('06-winner.png')
            main.GAME.execute('/api/v1/attempt',{});sync();scroll('#leaderboard');photo('07-leaderboard.png')
            js('document.querySelector("#copy-overlay").click()');photo('08-obs-setup.png');js('document.querySelector("#close-obs-dialog").click()')
            main.GAME.execute('/api/v1/session/pause',{})
            cdp('Emulation.setDeviceMetricsOverride',dict(width=1920,height=1080,deviceScaleFactor=1,mobile=False))
            cdp('Emulation.setDefaultBackgroundColorOverride',dict(color=dict(r=0,g=0,b=0,a=0)))
            cdp('Page.navigate',dict(url=base+'/overlay/demo'))
            wait('document.querySelector("#overlay-title")?.textContent.includes("Draw a mascot")')
            photo('09-obs-overlay.png',True)
    finally:
        if ws:ws.close()
        if browser:
            browser.terminate()
            try:browser.wait(timeout=5)
            except subprocess.TimeoutExpired:browser.kill();browser.wait()
        server.shutdown();server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome',default=os.getenv('CHROME_BIN') or shutil.which('chromium') or shutil.which('google-chrome') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    parser.add_argument('--output',type=Path,default=ROOT/'docs'/'screenshots')
    args=parser.parse_args();capture(args.chrome,args.output)
