"""Local fixture: all 11 agents installed, for the centre-mode label screenshot. No provider calls."""
import os
import sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _isolate  # noqa: F401  (must come before relay)
import relay
relay.DATA = Path(os.environ['RELAY_DATA']) / 'centre-browser' / uuid.uuid4().hex
relay.DATA.mkdir(parents=True)
ids = 'opus sonnet haiku strata sol astra terra luna spark grok gemini'.split()
cfg = relay.config()
view = [dict(id=k, provider=cfg['agents'][k]['provider'], model=cfg['agents'][k]['model'], tier=cfg['agents'][k]['tier'],
             installed=True, available=True, blocked=False) for k in ids]
relay.agents_view = lambda: view
relay.billing_view = lambda: {}
relay.coordinator = lambda: 'sol'
server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
