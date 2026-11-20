import os
﻿"""Local browser fixture. No scheduler, sign-ins or real provider calls."""
import sys, uuid, struct, zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: F401  (must come before relay)
import relay
relay.DATA = Path(os.environ['RELAY_DATA']) / 'attachment-browser' / uuid.uuid4().hex
relay.DATA.mkdir(parents=True, exist_ok=True)
image_path = relay.HERE / 'data' / 'attachment-test.png'
if not image_path.exists():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    rows = bytearray()
    for y in range(180):
        rows.append(0)
        for x in range(360):
            rows.extend((39, 169, 181) if 24 < x < 120 and 60 < y < 145 else (194, 146, 55) if 136 < x < 232 and 85 < y < 145 else (16, 23, 34))
    image_path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 360, 180, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(rows))) + chunk(b'IEND', b''))
relay.agents_view = lambda: [dict(id='sol',provider='codex',model='gpt-6.1-sol',tier=3,installed=True,available=True,blocked=False)]
relay.billing_view = lambda: {}
relay.coordinator = lambda: 'sol'
relay.repos = lambda: [str(relay.HERE)]
original_create = relay.create_goal
def create(body):
    with patch.object(relay, 'git', return_value='main'), patch.object(relay, 'blocked_reason', return_value=None), patch.object(relay, 'limited_reason', return_value=None):
        return original_create(body)
relay.create_goal = create
def chat_run(text):
    relay.chat_post('coordinator', 'Received the attached files.')
    c=relay.chat_load(); c['busy']=False; relay.write_json(relay.DATA / 'chat.json', c)
relay.chat_run=chat_run
g = relay.create_goal(dict(objective='Review the screenshot and implementation notes attached to this goal.',title='Attachment review',repo=str(relay.HERE), lead='sol'))
g['status']='needs_you'; g['reason']='Review the attached files, or attach another file in the note below.'
relay.save(g)
server=relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
