"""One elected EventSub receiver per workspace. No browser or chat-send tokens."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from .chat import parse_chat
from .twitch import TwitchFailure
from ..services.common import DomainError

EVENTSUB_URL='wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30'

class EventSubWorker:
    def __init__(self, game, accounts, connector=None, monotonic=time.monotonic):
        if connector is None:
            from websocket import create_connection
            connector=lambda url:create_connection(url,timeout=10,redirect_limit=0,enable_multithread=True)
        self.game,self.accounts,self.connector=game,accounts,connector
        self.monotonic=monotonic
        self.stop_event=threading.Event()
        self.payload={'state':'connecting','last_message_at':None}
        self.lease=None
        self.last_publish=0
        self.socket=None

    def snapshot(self):
        return self.game.repository.read_chat_status()

    def _status(self, state=None):
        changed=state is not None and self.payload['state']!=state
        if state is not None:self.payload['state']=state
        if self.lease is not None and (changed or self.monotonic()-self.last_publish>=5):
            self.game.repository.write_chat_status(self.lease,self.payload)
            self.last_publish=self.monotonic()

    @staticmethod
    def reconnect_url(url):
        if not isinstance(url,str):raise ValueError('invalid reconnect URL')
        parsed=urlparse(url)
        if parsed.scheme!='wss' or parsed.hostname!='eventsub.wss.twitch.tv' or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('invalid reconnect URL')
        return url  # Preserve Twitch's URL exactly; never log its query.

    @staticmethod
    def _read(socket):
        opcode,raw=socket.recv_data(control_frame=True)
        if opcode==8:raise ConnectionError('socket_closed')
        if opcode!=1:return None  # Library answers ping frames with pong automatically.
        if len(raw)>65536:raise ValueError('message_too_large')
        frame=json.loads(raw)
        if not isinstance(frame,dict) or not isinstance(frame.get('metadata'),dict) or not isinstance(frame.get('payload'),dict):
            raise ValueError('invalid_envelope')
        return frame

    def _open(self,url):
        from websocket import WebSocketTimeoutException
        socket=self.connector(url)
        socket.settimeout(1)
        deadline=self.monotonic()+10
        try:
            while not self.stop_event.is_set() and self.monotonic()<deadline:
                try:frame=self._read(socket)
                except (WebSocketTimeoutException,TimeoutError):continue
                if frame is None:continue
                if frame['metadata'].get('message_type')!='session_welcome':raise ValueError('welcome_required')
                session=frame['payload']['session']
                timeout=session['keepalive_timeout_seconds']
                if not isinstance(session['id'],str) or not session['id'] or type(timeout) is not int or not 10<=timeout<=600:
                    raise ValueError('invalid_welcome')
                return socket,session
            raise TimeoutError('welcome_timeout')
        except BaseException:
            socket.close(timeout=0)
            raise

    def _dispatch(self,frame,owner,subscription):
        kind=frame['metadata'].get('message_type')
        if kind=='notification':
            message=parse_chat(frame,owner,subscription)
            if message is not None:
                self.game.ingest_chat(message)
        elif kind=='revocation' and isinstance(frame['payload'].get('subscription'),dict) and frame['payload']['subscription'].get('id')==subscription:
            reason=frame['payload']['subscription'].get('status')
            if reason in ('authorization_revoked','user_removed'):
                self.accounts.invalidate_connection(owner)
            raise DomainError('eventsub_subscription_revoked',403)
        elif kind=='session_reconnect':
            return self.reconnect_url(frame['payload']['session']['reconnect_url'])
        return None

    def _eligible(self,owner):
        self.lease.execute('SELECT 1')  # A lost leader connection must stop intake.
        if self.game.chat_target()!=owner:raise ConnectionError('session_changed')
        self.accounts.access_token(owner)  # Detect local disconnect/revocation while socket is idle.

    def _session(self,owner):
        from websocket import WebSocketTimeoutException
        self.accounts.access_token(owner)  # Validate before the welcome/subscription deadline.
        self._status('connecting')
        socket,session=self._open(EVENTSUB_URL)
        self.socket=socket
        executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='eventsub-handover')
        pending=None
        orphan=None
        subscription=None
        last=self.monotonic()
        try:
            access=self.accounts.access_token(owner)
            try:
                subscription=self.accounts.provider.subscribe_chat(access,owner,session['id'])
            except TwitchFailure as error:
                if error.kind=='invalid':
                    self.accounts.access_token(owner,rejected_token=access)
                    raise ConnectionError('retry_subscription_with_new_token') from None
                raise
            self._status('connected')
            while not self.stop_event.is_set():
                self._eligible(owner)
                if pending and pending.done():
                    replacement,new_session=pending.result()
                    pending=None
                    orphan=replacement
                    # Drain already queued old-socket notifications before closing it.
                    socket.settimeout(.05)
                    drain_until=self.monotonic()+1
                    try:
                        while self.monotonic()<drain_until:
                            try:frame=self._read(socket)
                            except (WebSocketTimeoutException,TimeoutError,ConnectionError):break
                            if frame:self._dispatch(frame,owner,subscription)
                    finally:socket.close(timeout=0)
                    socket,session=replacement,new_session
                    orphan=None
                    self.socket=socket
                    last=self.monotonic()
                    self._status('connected')
                    # Twitch migrated the subscriptions: do NOT create another one.
                if self.monotonic()-last>session['keepalive_timeout_seconds']:
                    raise TimeoutError('keepalive_timeout')
                try:frame=self._read(socket)
                except (WebSocketTimeoutException,TimeoutError):
                    self._status()
                    continue
                if frame is None:continue  # Ping/pong isn't an EventSub keepalive.
                last=self.monotonic()
                self.payload['last_message_at']=time.time()
                reconnect=self._dispatch(frame,owner,subscription)
                if reconnect and pending is None:
                    self._status('reconnecting')
                    pending=executor.submit(self._open,reconnect)
                self._status()
        finally:
            socket.close(timeout=0)
            self.socket=None
            if orphan:orphan.close(timeout=0)
            executor.shutdown(wait=True,cancel_futures=True)
            if pending and not pending.cancelled():
                try:pending.result()[0].close(timeout=0)
                except Exception:pass

    def run(self):
        from websocket import WebSocketException
        delay=1
        while not self.stop_event.is_set():
            try:
                with self.game.repository.ingestion_leader() as lease:
                    if lease is None:
                        self.stop_event.wait(2)
                        continue
                    self.lease=lease
                    while not self.stop_event.is_set():
                        owner=self.game.chat_target()
                        if not owner:
                            self._status('waiting_session')
                            self.stop_event.wait(1)
                            continue
                        try:
                            self._session(owner)
                            delay=1
                        except DomainError as error:
                            self._status('reconnect_required' if error.status==401 else 'permission_required' if error.status==403 else 'reconnecting')
                        except TwitchFailure as error:
                            self._status('permission_required' if error.kind=='forbidden' else 'reconnecting')
                        except (WebSocketException,OSError,ValueError,KeyError,TypeError):
                            self._status('reconnecting')
                        self.stop_event.wait(delay)
                        delay=min(delay*2,30)
            except DomainError:
                self.stop_event.wait(2)
            finally:self.lease=None

    def start(self):
        thread=threading.Thread(target=self.run,name='twitch-eventsub',daemon=True)
        thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        if self.socket:
            self.socket.abort()
