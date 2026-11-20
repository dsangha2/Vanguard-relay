"""Stand-in agent for tests. Speaks Claude Code's stream-json shape. Behaviour is picked by the model name."""
import json, sys, os, uuid, time
model = sys.argv[1]; prompt = sys.stdin.read()
sid = sys.argv[sys.argv.index('--resume') + 1] if '--resume' in sys.argv else uuid.uuid4().hex
def say(text): print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}}), flush=True)
def end(text, relay=None):
    if relay: text += '\nRELAY: ' + json.dumps(relay)
    print(json.dumps({'type': 'result', 'result': text, 'session_id': sid}), flush=True)
print(json.dumps({'type': 'system', 'session_id': sid}), flush=True)
if model == 'lead':
    if not os.path.exists('feature.txt'):
        say('Planning. Handing the file to the helper.')
        end('Plan made.', {'next': 'pass', 'to': os.environ.get('FAKE_HELPER', 'helper'), 'note': 'Create feature.txt containing hello'})
    else:
        say('Checked the helper work: ' + open('feature.txt').read().strip() + ('; resumed' if '--resume' in sys.argv else ''))
        end('All good.' + (' (resumed)' if '--resume' in sys.argv else ''), {'next': 'done', 'note': ''})
elif model == 'helper':
    assert 'Create feature.txt' in prompt, prompt
    open('feature.txt', 'w').write('hello\n'); say('Wrote feature.txt')
    end('Done my part.', {'next': 'done', 'note': 'feature.txt written'})
elif model == 'breaker':
    open('broken.flag', 'w').write('x'); end('Changed things.', {'next': 'done', 'note': ''})
elif model == 'silent':
    end('I did something but gave no hand-off line.')
elif model == 'crash':
    sys.exit(3)
elif model == 'asker':
    end('Need a decision.', {'next': 'ask', 'note': 'Blue or green?'})
elif model == 'overage':
    print(json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'isUsingOverage': True}}), flush=True)
    time.sleep(30)
elif model == 'limited':      # what Claude prints when the 5-hour limit is hit, then a non-zero exit
    msg = "You've hit your 5-hour limit · resets 5:40am (America/Chicago)"
    say(msg)
    print(json.dumps({'type': 'result', 'is_error': True, 'result': msg, 'session_id': sid}), flush=True)
    sys.exit(1)
elif model == 'warner':       # finishes fine, but Claude's stream says the limit is nearly reached; asks for another turn
    print(json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed_warning', 'resetsAt': int(time.time()) + 7200, 'rateLimitType': 'five_hour',
                                                                      'utilization': 0.93, 'surpassedThreshold': 0.9, 'isUsingOverage': False}}), flush=True)
    end('Nearly at the limit.', {'next': 'continue', 'note': 'Create feature.txt containing hello'})
elif model == 'finisher':     # does the job, reports whether it saw the note, then asks a question so the run stops
    seen = 'yes' if 'Create feature.txt' in prompt else 'no'
    open('feature.txt', 'w').write('hello\n'); say('NOTE SEEN: ' + seen)
    end('Did it.', {'next': 'ask', 'note': 'note seen: ' + seen})
elif model == 'planner':      # a project lead: plans pieces, or shortens the notes
    if 'shared notes of a coding project' in prompt:
        end('# Short notes\n\n' + 'Kept every decision. ' * 20)
    else:
        n = int(os.environ.get('FAKE_PIECES', '3'))
        pieces = [{'title': f'Part {i}', 'what': f'Create part{i}.txt. Check it exists.', 'depends_on': [i - 1] if i > 1 else []} for i in range(1, n + 1)]
        end('Here is the plan.\n' + json.dumps({'pieces': pieces}))
elif model == 'piecer':       # a piece's lead: writes part<N>.txt, notes whether the previous piece's notes reached it
    import re as _re
    n = int(_re.search(r'PIECE (\d+) OF', prompt).group(1))
    seen = 'yes' if f'## Piece {n - 1}:' in prompt else 'no'
    if os.environ.get('FAKE_FAIL_PIECE') == str(n):
        end('Stuck.', {'next': 'ask', 'note': f'Piece {n} needs a decision'})
    else:
        open(f'part{n}.txt', 'w').write(f'part {n}; previous notes seen: {seen}\n')
        end('Done.', {'next': 'done', 'note': '', 'summary': {'built': [f'Part {n} exists'], 'not_tested': [], 'watch_out': [f'name chosen: part{n}']}})
elif model == 'sleeper':
    time.sleep(60)
elif model == 'coord':
    said = prompt.split('THE PERSON SAYS')[-1]
    assert ('You are the Vanguard coordinator' in prompt) != ('--resume' in sys.argv), 'rules go only into a fresh session'
    if 'start' in said:
        repo = json.loads(prompt.split('LIVE STATUS\n')[1].split('\n\nTHE PERSON SAYS')[0])['projects'][0]
        end('On it.\nACTIONS: ' + json.dumps([{'do': 'create_goal', 'repo': repo, 'title': 'From chat', 'objective': 'Add the feature file', 'lead': 'lead', 'team': ['helper']}, {'do': 'stop', 'goal': 'g00000000'}]))
    else:
        end('Nothing is running.')
