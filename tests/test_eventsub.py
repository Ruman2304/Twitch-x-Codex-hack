"""EventSub envelope, lifecycle, durable inbox and receiver protocol tests."""
import copy
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from unittest.mock import Mock,patch
from apps.api.app.integrations.chat import parse_chat
from apps.api.app.integrations.eventsub import EventSubWorker,EVENTSUB_URL
from apps.api.app.services.sessions import SessionService
from apps.api.app.persistence.repository import MemoryRepository,PostgresRepository
from test_backend import Clock

def notification(clock,option=1,viewer='1001',name='ViewerOne'):
    return {'metadata':{'message_type':'notification','subscription_type':'channel.chat.message','subscription_version':'1',
            'message_id':str(uuid.uuid4()),'message_timestamp':datetime.fromtimestamp(clock(),timezone.utc).isoformat()},
            'payload':{'subscription':{'id':'sub-1','type':'channel.chat.message','version':'1','status':'enabled',
                'condition':{'broadcaster_user_id':'42','user_id':'42'},'transport':{'method':'websocket','session_id':'socket-1'}},
                'event':{'broadcaster_user_id':'42','chatter_user_id':viewer,'chatter_user_name':name,
                         'message_id':str(uuid.uuid4()),'message':{'text':f'!vote {option}'}}}}

def welcome(sid='socket-1'):
    return {'metadata':{'message_type':'session_welcome'},'payload':{'session':{'id':sid,'keepalive_timeout_seconds':10}}}

class ParserTests(unittest.TestCase):
    def setUp(self):self.clock=Clock()
    def test_only_strict_vote_commands_are_accepted(self):
        for text,expected in [('!vote 1',1),(' !VOTE  2 ',2),('!vote 3',3),('!vote 4',None),('!vote1',None),('!vote 1 please',None),('!vote\n1',None),('hello',None),('!vote 1\n',None)]:
            with self.subTest(text=text):
                frame=notification(self.clock);frame['payload']['event']['message']['text']=text
                parsed=parse_chat(frame,'42','sub-1')
                self.assertEqual(parsed['option'] if parsed else None,expected)
    def test_wrong_channel_subscription_transport_and_shared_chat_are_ignored(self):
        edits=[('event','broadcaster_user_id','other'),('event','source_broadcaster_user_id','other'),('subscription','id','other'),('subscription','type','channel.follow')]
        for section,key,value in edits:
            frame=notification(self.clock);frame['payload'][section][key]=value
            self.assertIsNone(parse_chat(frame,'42','sub-1'))
        frame=notification(self.clock);frame['payload']['subscription']['condition']['user_id']='other'
        self.assertIsNone(parse_chat(frame,'42','sub-1'))
    def test_malformed_envelopes_and_timestamps_are_ignored(self):
        for frame in [None,{},[],{'metadata':[],'payload':{}},notification(self.clock,viewer='browser-supplied-name')]:
            self.assertIsNone(parse_chat(frame,'42','sub-1'))
        frame=notification(self.clock);frame['metadata']['message_timestamp']='not-a-time'
        self.assertIsNone(parse_chat(frame,'42','sub-1'))

class SubscriptionTests(unittest.TestCase):
    def test_subscription_uses_user_token_and_websocket_session(self):
        import io
        from apps.api.app.integrations.twitch import TwitchClient
        captured=[]
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        def opener(request,timeout):
            captured.append(request)
            return Response(b'{"data":[{"id":"sub-1","status":"enabled"}]}')
        provider=TwitchClient('client-id','private-client-secret','callback',opener)
        self.assertEqual(provider.subscribe_chat('private-user-token','42','socket-1'),'sub-1')
        request=captured[0];payload=json.loads(request.data)
        self.assertEqual(payload['condition'],{'broadcaster_user_id':'42','user_id':'42'})
        self.assertEqual(payload['transport'],{'method':'websocket','session_id':'socket-1'})
        self.assertEqual(request.get_header('Authorization'),'Bearer private-user-token')
        self.assertNotIn('private-client-secret',request.data.decode())
        self.assertNotIn('private-user-token',request.full_url)

class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.repo=MemoryRepository();self.game=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.game.execute('/api/v1/sessions',{},'42')
        for row in self.game.snapshot('42')['challenges']:
            self.game.execute('/api/v1/challenges/'+row['id']+'/approval',{'approval':'approved'},'42')
        self.game.execute('/api/v1/rounds',{},'42')
    def send(self,frame):return self.game.ingest_chat(parse_chat(frame,'42','sub-1'))
    def state(self):return self.game.snapshot('42')
    def test_two_real_identities_vote_and_change_without_extra_points(self):
        self.assertEqual(self.send(notification(self.clock,1)),'counted')
        self.send(notification(self.clock,2,viewer='1002',name='ViewerTwo'))
        self.send(notification(self.clock,3))
        self.assertEqual(self.state()['tallies'],{'1':0,'2':1,'3':1})
        self.assertEqual([r['points'] for r in self.state()['scores']],[1,1])
    def test_both_provider_id_and_chat_message_id_deduplicate(self):
        first=notification(self.clock,1)
        self.send(first)
        self.send(notification(self.clock,2))
        self.assertEqual(self.send(first),'duplicate')
        redelivery=copy.deepcopy(first);redelivery['metadata']['message_id']=str(uuid.uuid4())
        self.assertEqual(self.send(redelivery),'duplicate')
        self.assertEqual(self.state()['tallies'],{'1':0,'2':1,'3':0})
    def test_late_paused_and_previous_round_events_do_not_count(self):
        during_pause=notification(self.clock)
        self.game.execute('/api/v1/session/pause',{},'42')
        self.assertEqual(self.send(during_pause),'ignored')
        self.clock.advance(10)
        self.game.execute('/api/v1/session/resume',{},'42')
        delayed=notification(Clock())
        self.assertEqual(self.send(delayed),'ignored')
        self.assertEqual(self.send(during_pause),'duplicate')
        self.clock.advance(61)
        self.assertEqual(self.send(notification(self.clock)),'ignored')
        self.assertEqual(sum(self.state()['tallies'].values()),0)
    def test_future_timestamp_and_nonexistent_option_do_not_count(self):
        future=Clock();future.advance(40)
        self.assertEqual(self.send(notification(future)),'ignored')
        record=parse_chat(notification(self.clock),'42','sub-1');record['option']=4
        self.assertEqual(self.game.ingest_chat(record),'ignored')
    def test_same_display_name_does_not_merge_different_viewers(self):
        self.send(notification(self.clock,viewer='1001',name='SameName'))
        self.send(notification(self.clock,viewer='1002',name='SameName'))
        self.assertEqual(self.state()['scores'],[{'name':'SameName','points':1},{'name':'SameName','points':1}])
    def test_inbox_and_vote_rollback_together(self):
        frame=notification(self.clock)
        with patch('apps.api.app.services.voting.vote',side_effect=RuntimeError('injected_failure')):
            with self.assertRaises(RuntimeError):self.send(frame)
        self.assertEqual(self.send(frame),'counted')
        self.assertEqual(self.state()['scores'][0]['points'],1)
    def test_duplicate_after_service_recreation_does_not_count(self):
        frame=notification(self.clock);self.send(frame)
        self.game=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.assertEqual(self.send(frame),'duplicate')
    def test_duplicate_across_instances_is_atomic(self):
        other=SessionService(demo=False,clock=self.clock,repository=self.repo)
        record=parse_chat(notification(self.clock),'42','sub-1')
        with ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda n:(self.game if n%2 else other).ingest_chat(record),range(20)))
        self.assertEqual(result.count('counted'),1)
        self.assertEqual(result.count('duplicate'),19)
    def test_demo_and_wrong_channel_cannot_ingest(self):
        demo=SessionService(demo=True,clock=self.clock)
        message=parse_chat(notification(self.clock),'42','sub-1')
        self.assertEqual(demo.ingest_chat(message),'ignored')
        message['broadcaster_id']='other'
        self.assertEqual(self.game.ingest_chat(message),'ignored')

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'),'Set TEST_DATABASE_URL for persistent EventSub tests')
class PostgresIngestionTests(IngestionTests):
    @classmethod
    def setUpClass(cls):PostgresRepository(os.environ['TEST_DATABASE_URL']).migrate()
    def setUp(self):
        self.clock=Clock()
        self.repo=PostgresRepository(os.environ['TEST_DATABASE_URL'],'chat-'+str(uuid.uuid4()),demo=False)
        self.game=SessionService(demo=False,clock=self.clock,repository=self.repo)
        self.game.execute('/api/v1/sessions',{},'42')
        for row in self.game.snapshot('42')['challenges']:
            self.game.execute('/api/v1/challenges/'+row['id']+'/approval',{'approval':'approved'},'42')
        self.game.execute('/api/v1/rounds',{},'42')
    def test_single_ingestion_leader_and_takeover(self):
        with self.repo.ingestion_leader() as first:
            self.assertIsNotNone(first)
            with self.repo.ingestion_leader() as second:self.assertIsNone(second)
            self.repo.write_chat_status(first,{'state':'connected'})
            self.assertEqual(self.repo.read_chat_status()['state'],'connected')
        with self.repo.ingestion_leader() as third:self.assertIsNotNone(third)

try:
    from websocket import WebSocketTimeoutException
except ImportError:
    WebSocketTimeoutException=None

class FakeSocket:
    def __init__(self,frames,clock,on_empty=None):self.frames=list(frames);self.clock=clock;self.on_empty=on_empty;self.closed=False
    def settimeout(self,value):self.timeout=value
    def close(self,**kwargs):self.closed=True
    def recv_data(self,control_frame):
        self.clock.advance(.1)
        if not self.frames:
            if self.on_empty:self.on_empty()
            raise WebSocketTimeoutException('idle')
        frame=self.frames.pop(0)
        if isinstance(frame,Exception):raise frame
        if isinstance(frame,tuple):return frame
        return 1,json.dumps(frame).encode()

class ImmediateExecutor:
    def __init__(self,**kwargs):pass
    def submit(self,function,*args):
        from concurrent.futures import Future
        result=Future()
        try:result.set_result(function(*args))
        except Exception as error:result.set_exception(error)
        return result
    def shutdown(self,**kwargs):pass

@unittest.skipIf(WebSocketTimeoutException is None,'Install websocket-client for receiver tests')
class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.game=Mock();self.accounts=Mock()
        self.game.chat_target.return_value='42'
        self.accounts.access_token.return_value='server-only'
        self.accounts.provider.subscribe_chat.return_value='sub-1'
    def worker(self,connector):
        worker=EventSubWorker(self.game,self.accounts,connector,monotonic=self.clock)
        worker.lease=Mock()
        return worker
    def test_welcome_can_arrive_after_socket_timeout(self):
        socket=FakeSocket([WebSocketTimeoutException('wait'),welcome()],self.clock)
        worker=self.worker(lambda url:socket)
        self.assertEqual(worker._open(EVENTSUB_URL)[1]['id'],'socket-1')
    def test_reconnect_keeps_old_notifications_and_does_not_resubscribe(self):
        reconnect='wss://eventsub.wss.twitch.tv/ws?reconnect=opaque'
        handover={'metadata':{'message_type':'session_reconnect'},'payload':{'session':{'reconnect_url':reconnect}}}
        old=FakeSocket([welcome(),handover,notification(self.clock,1)],self.clock)
        new=FakeSocket([welcome('socket-2'),notification(self.clock,2)],self.clock)
        sockets=iter([old,new]);urls=[]
        def connect(url):urls.append(url);return next(sockets)
        worker=self.worker(connect);new.on_empty=worker.stop_event.set
        with patch('apps.api.app.integrations.eventsub.ThreadPoolExecutor',ImmediateExecutor):worker._session('42')
        self.assertEqual(urls,[EVENTSUB_URL,reconnect])
        self.accounts.provider.subscribe_chat.assert_called_once_with('server-only','42','socket-1')
        self.assertEqual(self.game.ingest_chat.call_count,2)
        self.assertTrue(old.closed and new.closed)
    def test_ping_frames_do_not_reset_eventsub_keepalive_deadline(self):
        socket=FakeSocket([welcome(),(9,b'ping')],self.clock)
        original=socket.recv_data
        def receive(control_frame):
            result=original(control_frame)
            if result[0]==9:self.clock.advance(11)
            return result
        socket.recv_data=receive
        worker=self.worker(lambda url:socket)
        with self.assertRaises(TimeoutError):worker._session('42')
        self.assertTrue(socket.closed)

    def test_reconnect_url_must_be_exact_twitch_tls_host(self):
        for url in ['ws://eventsub.wss.twitch.tv/ws','wss://evil.example/ws','wss://eventsub.wss.twitch.tv.evil/ws','wss://user@eventsub.wss.twitch.tv/ws']:
            with self.subTest(url=url),self.assertRaises(ValueError):EventSubWorker.reconnect_url(url)
    def test_revocation_invalidates_connection(self):
        worker=self.worker(None)
        frame={'metadata':{'message_type':'revocation'},'payload':{'subscription':{'id':'sub-1','status':'authorization_revoked'}}}
        with self.assertRaises(Exception):worker._dispatch(frame,'42','sub-1')
        self.accounts.invalidate_connection.assert_called_once_with('42')
    def test_subscription_401_refreshes_before_new_connection(self):
        socket=FakeSocket([welcome()],self.clock)
        worker=self.worker(lambda url:socket)
        from apps.api.app.integrations.twitch import TwitchFailure
        self.accounts.provider.subscribe_chat.side_effect=TwitchFailure('invalid')
        with self.assertRaises(ConnectionError):worker._session('42')
        self.accounts.access_token.assert_called_with('42',rejected_token='server-only')
        self.assertTrue(socket.closed)

if __name__=='__main__':unittest.main()
