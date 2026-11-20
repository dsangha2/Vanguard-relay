import os
﻿"""Local browser fixture. No scheduler, sign-ins or real provider calls."""
import sys, uuid, struct, zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: F401  (must come before relay)
import relay
relay.DATA = Path(os.environ['RELAY_DATA']) / 'brief-browser' / uuid.uuid4().hex
relay.DATA.mkdir(parents=True, exist_ok=True)
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
g['status']='ready'; g['reason']='Ready for review'
g['title']='Merge brief and page restart'
g['summary']={'built':['Review what changed before merging.', 'Restart Relay from this page.', 'Recognize Relay by its new app icon.'], 'not_tested':['Installing the app on a physical phone.'], 'watch_out':[]}
g['turns']=[dict(n=1, agent='sol',model='gpt-6.1-sol',status='ok',started=0,ended=1,final='Finished the requested improvements.',files=['relay.py','web/index.html'],stat='2 files changed, 112 insertions(+), 9 deletions(-)', tests={'verdict':'pass','summary':'Tests pass'})]
relay.code_stale=lambda: True
relay.save(g)
server=relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
