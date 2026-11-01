"""Convert authenticated Twitch notifications into a minimal vote command."""
import re
from datetime import datetime

def parse_chat(frame, broadcaster_id, subscription_id):
    try:
        meta, payload = frame['metadata'], frame['payload']
        sub, event = payload['subscription'], payload['event']
        if (meta['message_type']!='notification' or meta['subscription_type']!='channel.chat.message' or
            meta['subscription_version']!='1' or sub['type']!='channel.chat.message' or sub['version']!='1' or
            sub['id']!=subscription_id or sub['status']!='enabled' or sub['transport']['method']!='websocket' or
            sub['condition']['broadcaster_user_id']!=broadcaster_id or sub['condition']['user_id']!=broadcaster_id or
            event['broadcaster_user_id']!=broadcaster_id or event.get('source_broadcaster_user_id') not in (None,'',broadcaster_id)):
            return None
        text = event['message']['text']
        if not isinstance(text,str) or len(text)>500:
            return None
        match = re.fullmatch(r'!vote[ \t]+([1-3])',text.strip(' \t'),re.IGNORECASE)
        if not match:
            return None
        viewer, name = event['chatter_user_id'], event['chatter_user_name']
        event_id, chat_id = meta['message_id'], event['message_id']
        if not all(isinstance(v,str) and 0<len(v)<=128 for v in (viewer,name,event_id,chat_id)) or not re.fullmatch(r'[0-9]+',viewer):
            return None
        sent = datetime.fromisoformat(meta['message_timestamp'].replace('Z','+00:00'))
        if sent.tzinfo is None:
            return None
        return dict(broadcaster_id=broadcaster_id,viewer_id=viewer,display_name=name,
                    event_id=event_id,chat_id=chat_id,option=int(match[1]),sent_at=sent.timestamp())
    except (KeyError,TypeError,ValueError,AttributeError):
        return None
