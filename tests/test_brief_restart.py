"""Focused coverage for the merge brief and self-restart behavior."""
import hashlib
import io
import http.client
import json
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import _isolate  # noqa: F401  (must come before relay)
import relay


class BriefTests(unittest.TestCase):
    def test_summary_normalization_caps_items_and_uses_final_as_fallback(self):
        raw = {
            'built': ['  Built the page  ', 'x' * 400] + ['extra'] * 10,
            'not_tested': ['manual install'],
            'watch_out': [],
        }
        got = relay.normalize_summary(raw)
        self.assertEqual(got['built'][0], 'Built the page')
        self.assertEqual(len(got['built']), 8)
        self.assertEqual(len(got['built'][1]), 300)
        self.assertEqual(got['not_tested'], ['manual install'])
        self.assertNotIn('fallback', got)

        final = 'A' * 700
        fallback = relay.normalize_summary(None, final)
        self.assertEqual(fallback['built'], [final[:600]])
        self.assertTrue(fallback['fallback'])

    def test_proposal_preserves_normalized_summary_and_done_stores_it(self):
        line = ('RELAY: {"next":"done","note":"finished",'
                '"summary":{"built":["Added a useful screen"],'
                '"not_tested":["Phone install"],"watch_out":[]}}')
        prop = relay.proposal(line)
        self.assertEqual(prop['summary']['built'], ['Added a useful screen'])
        self.assertEqual(prop['summary']['not_tested'], ['Phone install'])
        goal = {'lead': 'lead', 'turns': [], 'summary': None}
        turn = {'agent': 'lead', 'status': 'ok', 'proposal': prop, 'tests': {'verdict': 'pass'}, 'final': line}
        result = relay.decide(goal, turn)
        self.assertEqual(result[0], 'ready')
        self.assertEqual(goal['summary']['built'], ['Added a useful screen'])

    def test_ready_goal_summary_is_returned_in_detail_and_list_shape(self):
        data = {'id': 'g1', 'status': 'ready', 'objective': 'Ship it', 'lead': 'lead',
                'title': 'Ship it', 'repo': '.', 'team': ['lead'], 'turns': [], 'handoffs': 0,
                'cap': 6, 'mode': 'auto', 'summary': {
                    'built': ['A clear summary'], 'not_tested': [], 'watch_out': ['Check on mobile']}}
        result = relay.summary(data)
        self.assertEqual(result['summary'], data['summary'])

    def test_legacy_done_falls_back_and_coordinator_can_read_it(self):
        final = 'You can now review the finished work.\nRELAY: {"next":"done"}'
        prop = relay.proposal(final)
        goal = dict(id='g12345678', title='Review', repo=str(relay.HERE), lead='sol', team=['sol'],
                    status='ready', mode='auto', turns=[], handoffs=0, test_cmd='')
        turn = dict(agent='sol', status='ok', proposal=prop, tests={}, final=final)
        self.assertEqual(relay.decide(goal, turn)[0], 'ready')
        self.assertEqual(goal['summary']['built'], [final[:600]])
        self.assertTrue(goal['summary']['fallback'])
        with patch.object(relay, 'all_goals', return_value=[goal]), patch.object(relay, 'repos', return_value=[]), patch.object(relay, 'agents_view', return_value=[]):
            self.assertEqual(relay.status_block()['goals'][0]['summary'], goal['summary'])



class RestartTests(unittest.TestCase):
    def test_restart_request_refuses_running_queued_or_busy_chat(self):
        with patch.object(relay, 'all_goals', return_value=[{'status': 'running'}]), \
                patch.object(relay, 'chat_load', return_value={'busy': False}), \
                self.assertRaisesRegex(ValueError, 'running'):
            relay.request_restart(Mock())

        with patch.object(relay, 'all_goals', return_value=[{'status': 'queued'}]), \
                patch.object(relay, 'chat_load', return_value={'busy': False}), \
                self.assertRaisesRegex(ValueError, 'queued'):
            relay.request_restart(Mock())

        with patch.object(relay, 'all_goals', return_value=[]), \
                patch.object(relay, 'chat_load', return_value={'busy': True}), \
                self.assertRaisesRegex(ValueError, 'chat'):
            relay.request_restart(Mock())

    def test_merge_schedules_restart_after_response_only_when_idle(self):
        handler = object.__new__(relay.Handler)
        handler.path = '/api/goals/g12345678/merge'
        handler.headers = {'Content-Length': '2'}
        handler.server = Mock()
        handler.origin_ok = Mock(return_value=True)
        events = []
        handler.send = Mock(side_effect=lambda code, value: events.append(('response', code, value)))
        with patch.object(relay, 'merge_goal', return_value={'relay_changed': True}), patch.object(relay, 'summary', return_value={}), patch.object(relay, 'RESTARTING', False), patch.object(relay, 'restart_allowed') as allowed, patch.object(relay, 'request_restart', side_effect=lambda server, delay: events.append(('restart', delay))):
            handler.rfile = io.BytesIO(b'{}')
            handler.do_POST()
            self.assertEqual(events, [('response', 200, {'restart_scheduled': True}), ('restart', 2)])
            events.clear()
            allowed.side_effect = ValueError('Goals are busy')
            handler.rfile = io.BytesIO(b'{}')
            handler.do_POST()
            self.assertEqual(events, [('response', 200, {'restart_scheduled': False})])

    def test_restart_endpoint_enforces_origin_and_refuses_busy_work(self):
        handler = object.__new__(relay.Handler)
        handler.path = '/api/restart'
        handler.headers = {'Content-Length': '2'}
        handler.server = Mock()
        handler.send = Mock()
        handler.origin_ok = Mock(return_value=False)
        with patch.object(relay, 'request_restart') as restart:
            handler.do_POST()
            self.assertEqual(handler.send.call_args.args[0], 403)
            restart.assert_not_called()
            handler.origin_ok.return_value = True
            handler.rfile = io.BytesIO(b'{}')
            with patch.object(relay, 'all_goals', return_value=[{'status': 'running'}]):
                handler.do_POST()
            self.assertEqual(handler.send.call_args.args[0], 400)
            self.assertIn('running', handler.send.call_args.args[1]['error'])
            restart.assert_not_called()

    def test_stale_detection_compares_loaded_hash_to_current_file(self):
        with patch.object(relay, 'LOADED_HASH', 'loaded'), \
                patch.object(relay, 'HERE', relay.Path(__file__).resolve().parents[1]), \
                patch.object(relay, 'LOADED_VERSION', 'abc123'):
            with patch.object(relay.Path, 'read_bytes', return_value=b'changed'):
                self.assertTrue(relay.code_stale())
            with patch.object(relay.Path, 'read_bytes', return_value=b'loaded'):
                with patch.object(relay, 'LOADED_HASH', hashlib.sha256(b'loaded').hexdigest()):
                    self.assertFalse(relay.code_stale())

    def test_restart_child_command_and_environment_without_launching_process(self):
        fake_process = Mock()
        stdout_file, stderr_file = Mock(), Mock()
        stdout_context, stderr_context = Mock(), Mock()
        stdout_context.__enter__ = Mock(return_value=stdout_file)
        stdout_context.__exit__ = Mock(return_value=False)
        stderr_context.__enter__ = Mock(return_value=stderr_file)
        stderr_context.__exit__ = Mock(return_value=False)
        group_flag = relay.subprocess.CREATE_NEW_PROCESS_GROUP
        nowindow_flag = relay.subprocess.CREATE_NO_WINDOW
        flags = group_flag | nowindow_flag
        with patch.object(relay, 'HERE', relay.Path(__file__).resolve().parents[1]), \
                patch.object(relay, 'DATA', relay.Path(__file__).resolve().parents[1] / 'data'), \
                patch.object(relay, 'subprocess') as subprocess, \
                patch.object(relay.Path, 'mkdir'), \
                patch.object(relay.Path, 'open', autospec=True,
                             side_effect=[stdout_context, stderr_context]) as open_file:
            subprocess.CREATE_NEW_PROCESS_GROUP = group_flag
            subprocess.CREATE_NO_WINDOW = nowindow_flag
            subprocess.Popen.return_value = fake_process
            relay.spawn_restart_child()
        args, kwargs = subprocess.Popen.call_args
        self.assertEqual(args[0], [sys.executable, '-B', str(relay.HERE / 'relay.py')])
        self.assertEqual(kwargs['cwd'], str(relay.HERE))
        self.assertEqual(kwargs['env']['RELAY_WAIT_PORT'], '1')
        self.assertEqual(kwargs['creationflags'], flags)
        self.assertEqual(kwargs['stdout'], stdout_file)
        self.assertEqual(kwargs['stderr'], stderr_file)
        self.assertEqual([call.args[0].name for call in open_file.call_args_list],
                         ['server.log', 'server.err.log'])

    def test_bind_retries_only_for_waiting_child_and_stops_after_deadline(self):
        server = Mock()
        with patch.object(relay.os, 'environ', {'RELAY_WAIT_PORT': '1'}), \
                patch.object(relay, 'ThreadingHTTPServer', side_effect=[OSError('busy'), server]) as bind, \
                patch.object(relay.time, 'sleep') as sleep:
            self.assertIs(relay.bind_server(8940), server)
        self.assertEqual(bind.call_count, 2)
        sleep.assert_called_once()

        with patch.object(relay.os, 'environ', {'RELAY_WAIT_PORT': '1'}), \
                patch.object(relay, 'ThreadingHTTPServer', side_effect=OSError('busy')), \
                patch.object(relay.time, 'monotonic', side_effect=[100, 131]), \
                self.assertRaisesRegex(OSError, 'busy'):
            relay.bind_server(8940)

    def test_asset_routes_are_whitelisted_and_cache_for_one_day(self):
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)

        def get(path):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            try:
                conn.request('GET', path)
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()

        status, headers, body = get('/assets/logo.svg')
        self.assertEqual(status, 200)
        self.assertIn('image/svg+xml', headers['Content-Type'])
        self.assertIn('max-age=86400', headers['Cache-Control'])
        self.assertIn(b'<svg', body)
        status, headers, body = get('/assets/apple-touch-icon.png')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertTrue(body.startswith(b'\x89PNG'))
        status, _, _ = get('/assets/other.txt')
        self.assertEqual(status, 404)
        status, headers, body = get('/manifest.webmanifest')
        self.assertEqual(status, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest['name'], 'Vanguard Relay')
        self.assertEqual(manifest['icons'][0]['src'], '/assets/apple-touch-icon.png')


if __name__ == '__main__':
    unittest.main()
