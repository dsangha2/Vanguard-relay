"""Real HTTP goal handlers with deterministic agents; no scheduler or provider calls."""
import os
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: F401  (must come before relay)
import relay

relay.DATA = Path(os.environ['RELAY_DATA']) / 'goal-browser' / uuid.uuid4().hex
relay.DATA.mkdir(parents=True)
cfg = relay.config()
view = [dict(id=k, provider=a['provider'], model=a['model'], tier=a['tier'],
             installed=True, available=True, blocked=False) for k, a in cfg['agents'].items()]
relay.agents_view = lambda: view
relay.billing_view = lambda: {}
relay.blocked_reason = lambda _: None
relay.limited_reason = lambda _: None
relay.strata_reachable = lambda: False
relay.coordinator = lambda: 'luna'
relay.chat_call = lambda *a, **k: (json.dumps(dict(lead='sol', team=['sol', 'terra', 'luna'],
                                               why='A careful lead with two affordable helpers.')), None, None)
relay.git = lambda *a, **k: 'main' if a[1:3] == ('branch', '--show-current') else ''
relay.notify = lambda *a, **k: None

g = relay.create_goal(dict(repo=str(relay.HERE), title='Build the project overview',
                           objective='Build a clear project overview and review the result.'))
g.update(status='needs_you', reason='luna asks: Which projects should appear first?',
         question=dict(agent='luna', text='Which projects should appear first?\nPlease include any names or priorities.'),
         plan=[dict(piece='Build the overview', who='luna', why='A narrow feature.'),
               dict(piece='Review the result', who='sol', why='Lead acceptance.')], plan_turn=1,
         turns=[dict(n=1, agent='sol', model='gpt-6.1-sol', status='ok', started=1, final='Planned the work.'),
                dict(n=2, agent='luna', model='gpt-6-luna', status='ok', started=2,
                     final='Ready to build; need project priorities.',
                     proposal=dict(next='ask', note='Which projects should appear first?'))])
relay.save(g)

server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
