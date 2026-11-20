import json
import http.client
import threading
from urllib.parse import quote
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolate  # noqa: F401  (must come before relay)
import relay


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'data'
        self.data.mkdir()
        self.data_patch = patch.object(relay, 'DATA', self.data)
        self.data_patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.data_patch.stop)

    def limits(self, **changes):
        relay.write_json(self.data / 'config.json', {'uploads': changes})

    def test_allowed_types_and_signatures(self):
        for name, data, kind in [
            ('a.png', b'\x89PNG\r\n\x1a\nimage', 'image/png'),
            ('a.jpg', b'\xff\xd8\xffimage', 'image/jpeg'),
            ('a.gif', b'GIF89aimage', 'image/gif'),
            ('a.webp', b'RIFF1234WEBPimage', 'image/webp'),
            ('a.pdf', b'%PDF-1.7', 'application/pdf'),
            ('a.md', 'hello \u2603'.encode(), 'text/plain'),
        ]:
            with self.subTest(name=name):
                item = relay.store_upload(name, data)
                self.assertEqual(item['kind'], kind)
                self.assertEqual((relay.upload_dir(item['id']) / item['name']).read_bytes(), data)
        for name, data in [('bad.png', b'not png'), ('bad.pdf', b'pdf'), ('bad.webp', b'RIFF1234NOPE'),
                           ('bad.txt', b'\xff'), ('bad.txt', b'MZbinary'), ('bad.txt', b'a\x00b'),
                           ('bad.exe', b'MZbinary'), ('bad.zip', b'PK')]:
            with self.subTest(name=name, data=data), self.assertRaises(ValueError):
                relay.store_upload(name, data)

    def test_names_and_ids_cannot_escape_storage(self):
        for name in ('../evil.txt', 'folder\\evil.txt', 'C:evil.txt', '.', '..', 'CON.txt', 'NUL', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                relay.store_upload(name, b'hello')
        collision = relay.store_upload('upload.json', b'{}')
        self.assertEqual(relay.upload_record(collision['id'])['name'], 'upload.json')
        self.assertEqual((relay.upload_dir(collision['id']) / 'upload.json').read_bytes(), b'{}')
        item = relay.store_upload('bad?name.txt', b'hello')
        self.assertEqual(item['name'], 'bad_name.txt')
        for uid in ('../x', 'a' * 31, 'A' * 32):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                relay.upload_record(uid)

    def test_symlink_escape_is_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        link = self.data / 'link'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('directory symlinks unavailable')
        with self.assertRaises(ValueError):
            relay.contained(self.data, 'link', 'escape.txt')

    def test_configured_limits_and_deduplication(self):
        self.limits(max_file_bytes=4, max_total_bytes=6, max_files=1)
        with self.assertRaises(ValueError):
            relay.store_upload('large.txt', b'12345')
        item = relay.store_upload('a.txt', b'1234')
        with self.assertRaises(ValueError):
            relay.store_upload('b.txt', b'123')
        with self.assertRaises(ValueError):
            relay.attachment_ids([item['id'], item['id']])
        self.assertEqual(relay.attachment_ids([item['id']]), [item['id']])
        with self.assertRaises(ValueError):
            relay.attachment_ids('not a list')

    def test_cleanup_retains_referenced_and_recent_uploads(self):
        old = relay.store_upload('old.txt', b'old')
        kept = relay.store_upload('kept.txt', b'kept')
        recent = relay.store_upload('recent.txt', b'recent')
        relay.attach_files([kept['id']], 'g12345678')
        for item in (old, kept):
            record = relay.upload_record(item['id'])
            record['created'] = time.time() - 86401
            relay.write_json(relay.upload_dir(item['id']) / '.upload-meta', record)
        relay.store_upload('trigger.txt', b'new upload triggers cleanup')
        self.assertFalse(relay.upload_dir(old['id']).exists())
        self.assertTrue(relay.upload_dir(kept['id']).exists())
        self.assertTrue(relay.upload_dir(recent['id']).exists())

    def test_goal_copy_brief_and_removal(self):
        gid = 'g12345678'
        image = relay.store_upload('photo.png', b'\x89PNG\r\n\x1a\nimage')
        text = relay.store_upload('notes.txt', b'notes')
        items = relay.attach_files([image['id'], text['id']], gid)
        goal = {'id': gid, 'attachments': items, 'notes': [{'attachments': [text]}]}
        self.assertEqual(len(relay.goal_attachments(goal)), 2)
        for item in items:
            path = relay.attachment_path(goal, item)
            self.assertTrue(path.is_file())
            self.assertTrue(path.is_relative_to(self.data / 'goals'))
            self.assertFalse(path.is_relative_to(self.data / 'work'))
        brief = relay.attachment_brief(items, lambda item: relay.attachment_path(goal, item))
        self.assertIn('photo.png', brief)
        self.assertIn('notes.txt', brief)
        self.assertIn('Do not pretend', brief)
        short = relay.attachment_brief(items, lambda item: relay.attachment_path(goal, item), short=True)
        self.assertIn('notes.txt', short)
        self.assertNotIn('photo.png', short)
        flags = relay.attachment_argv(['exec'], {'images': {'flag': '--image'}}, items, lambda item: relay.attachment_path(goal, item))
        self.assertEqual(flags, ['exec', '--image', str(relay.attachment_path(goal, image))])
        more = [relay.store_upload(f'{i}.txt', b'x') for i in range(4)]
        limited = relay.attachment_brief(more, lambda item: item['name'], short=True)
        self.assertIn('2.txt', limited)
        self.assertNotIn('3.txt', limited)
        relay.remove_goal_attachments(gid)
        self.assertFalse((self.data / 'goals' / gid / 'attachments').exists())
        self.assertFalse(relay.upload_dir(image['id']).exists())


    def test_chat_files_are_retained_until_messages_are_removed(self):
        item = relay.store_upload('chat.txt', b'message data')
        with patch.object(relay.threading, 'Thread'):
            relay.chat_send('', [item['id']])
        messages = relay.chat_load()['messages']
        self.assertEqual(messages[-1]['attachments'][0]['id'], item['id'])
        self.assertIn('chat', relay.upload_record(item['id'])['refs'])
        relay.write_json(self.data / 'chat.json', dict(messages=[], sessions={}, busy=False))
        relay.release_chat_attachments(messages)
        self.assertEqual(relay.upload_record(item['id'])['refs'], [])

    def test_strata_reads_only_staged_text_without_making_it_writable(self):
        import strata_run
        gid = 'g12345678'
        item = relay.store_upload('context.md', b'Attachment context')
        relay.attach_files([item['id']], gid)
        prompt_file = relay.goal_path(gid) / 'turn-1.prompt.md'
        goal = dict(id=gid, objective='Edit example.py', turns=[], notes=[], attachments=[item], test_cmd='')
        text = relay.short_brief(goal, '')
        block = strata_run.attachments_block(text, prompt_file)
        self.assertIn('Attachment context', block)
        self.assertIn('cannot view images', block)
        self.assertNotIn(str(relay.attachment_path(goal, item)), strata_run.parse_brief(text)['files'])
        bad = 'Files from the user (read them before starting)\n- ' + str(self.data / 'outside.txt') + ' (text/plain)\n'
        (self.data / 'outside.txt').write_text('secret')
        with self.assertRaises(ValueError): strata_run.attachments_block(bad, prompt_file)

    def test_windows_taskkill_failure_uses_direct_process_kill(self):
        from unittest.mock import Mock
        process = Mock(pid=123)
        process.poll.return_value = None
        with patch.object(relay.os, 'name', 'nt'), patch.object(relay.subprocess, 'run', return_value=Mock(returncode=1)):
            relay.kill_tree(process)
        process.kill.assert_called_once()



class AttachmentIntegrationTests(unittest.TestCase):
    setUp = AttachmentTests.setUp
    def test_create_and_act_attach_files(self):
        repo = Path(self.tmp.name) / 'repo'
        (repo / '.git').mkdir(parents=True)
        first = relay.store_upload('first.txt', b'first')
        second = relay.store_upload('second.txt', b'second')
        with patch.object(relay, 'git', return_value='abc'), patch.object(relay, 'blocked_reason', return_value=None), patch.object(relay, 'limited_reason', return_value=None):
            agent = next(iter(relay.config()['agents']))
            goal = relay.create_goal({'objective': 'Build a feature', 'repo': str(repo), 'lead': agent, 'attachments': [first['id']]})
            self.assertEqual(goal['attachments'][0]['id'], first['id'])
            self.assertTrue(relay.attachment_path(goal, first).is_file())
            goal['status'] = 'needs_you'
            relay.save(goal)
            updated = relay.act(goal['id'], {'action': 'continue', 'attachments': [second['id']]})
            self.assertEqual(updated['notes'][-1]['attachments'][0]['id'], second['id'])
            self.assertTrue(relay.attachment_path(updated, second).is_file())
            brief = relay.attachment_brief(relay.goal_attachments(updated), lambda item: relay.attachment_path(updated, item))
            self.assertIn('first.txt', brief)
            self.assertIn('second.txt', brief)

    def test_agent_turn_receives_paths_and_records_note_files(self):
        import sys
        repo = Path(self.tmp.name) / 'repo'; (repo / '.git').mkdir(parents=True)
        fake = Path(__file__).with_name('fake_agent.py').resolve()
        relay.write_json(self.data / 'config.json', {'agents': {'attachment-test': {'provider': 'attachment-fake', 'model': 'silent', 'tier': 1}},
            'providers': {'attachment-fake': {'exe': [sys.executable], 'stdin': True, 'argv': ['{exe}', str(fake), '{model}'], 'billing': {'guarded': True}}}})
        image = relay.store_upload('photo.png', b'\x89PNG\r\n\x1a\nimage')
        note = relay.store_upload('note.txt', b'Instructions')
        with patch.object(relay, 'git', return_value=''), patch.object(relay, 'blocked_reason', return_value=None), patch.object(relay, 'limited_reason', return_value=None):
            goal = relay.create_goal(dict(repo=str(repo), objective='Review attachments', lead='attachment-test', attachments=[image['id']]))
            Path(goal['work']).mkdir(parents=True, exist_ok=True)
            goal['notes'].append(dict(text='See note', delivered=False, attachments=relay.attach_files([note['id']], goal['id'])))
            relay.save(goal)
            turn = relay.run_turn(goal, 'attachment-test', '', 'Test')
        self.assertEqual(turn['attachments'][0]['id'], note['id'])
        prompt = (relay.goal_path(goal['id']) / 'turn-1.prompt.md').read_text(encoding='utf-8')
        self.assertIn(str(relay.attachment_path(goal, image)), prompt)
        self.assertIn(str(relay.attachment_path(goal, note)), prompt)
        self.assertIn('Image viewing is not confirmed', prompt)
        self.assertTrue(relay.load(goal['id'])['notes'][0]['delivered'])

    def test_http_upload_download_and_origin(self):
        server = relay.ThreadingHTTPServer(('127.0.0.1', 0), relay.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()
        headers = {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'X-File-Name': quote('snow \u2603.txt')}
        status, _, body = request('POST', '/api/uploads', b'hello', headers)
        self.assertEqual(status, 200, body)
        item = json.loads(body)
        self.assertEqual(item['name'], 'snow \u2603.txt')
        status, response_headers, body = request('GET', '/api/uploads/' + item['id'])
        self.assertEqual((status, body), (200, b'hello'))
        self.assertEqual(response_headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', response_headers['Content-Disposition'])
        self.assertEqual(request('POST', '/api/uploads', b'hello', {'X-File-Name': 'a.txt'})[0], 403)
        self.assertEqual(request('GET', '/api/uploads/' + '0' * 32)[0], 404)
        gid = 'g12345678'
        relay.save({'id': gid, 'status': 'needs_you', 'notes': [], 'turns': [], 'created': time.time(),
                    'title': 'Test', 'objective': 'Test goal', 'lead': 'x', 'team': ['x'],
                    'mode': 'manual', 'handoffs': 0, 'cap': 1, 'next': {'agent': 'x'},
                    'reason': '', 'suggestion': None})
        note_body = json.dumps({'attachments': [item['id']]}).encode()
        with patch.object(relay, 'summary', side_effect=lambda goal: goal):
            status, _, result = request('POST', '/api/goals/' + gid + '/note', note_body,
                                        {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'})
        self.assertEqual(status, 200, result)
        goal = relay.load(gid)
        self.assertEqual(goal['notes'][0]['text'], '')
        self.assertTrue(relay.attachment_path(goal, item).is_file())
        image = relay.store_upload('photo.png', b'\x89PNG\r\n\x1a\nimage')
        status, response_headers, body = request('GET', '/api/uploads/' + image['id'])
        self.assertEqual(status, 200)
        self.assertEqual(response_headers['Content-Type'], 'image/png')
        self.assertEqual(response_headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Content-Disposition', response_headers)
        self.assertEqual(request('GET', '/api/uploads/' + image['id'], headers={'Host': 'evil.example'})[0], 403)
        with patch.object(relay, 'summary', side_effect=lambda goal: goal):
            status, _, result = request('POST', '/api/goals/' + gid + '/archive', b'{}',
                {'X-Relay': '1', 'Sec-Fetch-Site': 'same-origin', 'Content-Type': 'application/json'})
        self.assertEqual(status, 200, result)
        self.assertFalse((relay.goal_path(gid) / 'attachments').exists())
        self.assertEqual(request('GET', '/api/uploads/' + item['id'])[0], 404)


if __name__ == '__main__':
    unittest.main()
