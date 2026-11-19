"""Channel persistence and immutable per-task configuration; all providers are offline fixtures."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app import agent_api, agent_engine, agent_settings, main
from tests import test_agent_api as api_tests


class ChannelHelpers:
    async def library(self):
        response = await self.client.get('/agent-settings/channels')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def add_channel(self, name='渠道A', key='channel-a-offline-secret', model='model-a'):
        before = await self.library()
        response = await self.client.post('/agent-settings/channels', json={
            'name': name, 'base_url': 'https://channel.example/v1/', 'model': model,
            'api_key': key, 'expected_revision': before['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return body['channels'][-1]

    async def activate(self, channel_id):
        response = await self.client.post('/agent-settings/channels/' + channel_id + '/activate',
            json={'expected_revision': (await self.library())['revision']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def edit_channel(self, channel_id, **updates):
        before = await self.library()
        existing = next(item for item in before['channels'] if item['id'] == channel_id)
        return await self.client.request('PUT', '/agent-settings/channels/' + channel_id, json_body={
            'name': existing['name'], 'expected_revision': before['revision'], **updates})


class ChannelStoreTests(ChannelHelpers, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'agent-settings.json'
        self.env = patch.dict(os.environ, {'THESISDEBATE_AGENT_SETTINGS_PATH': str(self.path),
            'AGENT_BASE_URL': '', 'AGENT_MODEL': '', 'AGENT_API_KEY': '',
            'OPENAI_BASE_URL': '', 'OPENAI_MODEL': '', 'OPENAI_API_KEY': ''})
        self.env.start()
        self.client = api_tests.ASGIClient(main.app)

    async def asyncTearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_environment_is_virtual_readonly_and_get_never_writes(self):
        with patch.dict(os.environ, {'AGENT_BASE_URL': 'https://env.example/v1', 'AGENT_MODEL': 'env-model', 'AGENT_API_KEY': 'env-secret'}):
            library = await self.library()
            self.assertEqual(library['active_channel_id'], 'environment')
            self.assertTrue(library['channels'][0]['configured'])
            self.assertTrue(library['channels'][0]['read_only'])
            self.assertNotIn('env-secret', json.dumps(library))
            denied = await self.edit_channel('environment', name='不能覆盖环境')
            self.assertEqual(denied.status_code, 403)
            self.assertFalse(self.path.exists())

    async def test_legacy_read_is_byte_preserving_and_first_write_migrates(self):
        credential = agent_settings._encode_key('legacy-only-secret')
        self.path.write_text(json.dumps({'version': 1, 'base_url': 'https://legacy.example/v1', 'model': 'legacy-model', 'credential': credential}), encoding='utf-8')
        before = self.path.read_bytes()
        library = await self.library()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(library['active_channel_id'], 'legacy-default')
        edited = await self.edit_channel('legacy-default', name='旧渠道重命名')
        self.assertEqual(edited.status_code, 200, edited.text)
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 2)
        self.assertEqual(stored['channels'][0]['credential'], credential)
        self.assertEqual(stored['channels'][0]['config_revision'], 1)
        self.assertEqual(agent_settings.resolve_config()['model'], 'legacy-model')

    async def test_create_save_and_activate_are_separate_and_empty_new_key_never_inherits(self):
        a = await self.add_channel()
        self.assertIsNone((await self.library())['active_channel_id'])
        self.assertFalse(agent_settings.public_config()['configured'])
        await self.activate(a['id'])
        with patch.dict(os.environ, {'AGENT_API_KEY': 'env-must-not-be-borrowed'}):
            b = await self.add_channel('待完善渠道', '', 'model-b')
        library = await self.library()
        self.assertEqual(library['active_channel_id'], a['id'])
        self.assertFalse(b['has_api_key'])
        denied = await self.client.post('/agent-settings/channels/' + b['id'] + '/activate', json={})
        self.assertEqual(denied.status_code, 422)
        self.assertEqual(agent_settings.resolve_config()['api_key'], 'channel-a-offline-secret')

    async def test_each_channel_owns_its_key_blank_preserves_and_clear_is_local(self):
        a = await self.add_channel()
        await self.activate(a['id'])
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        renamed = await self.edit_channel(b['id'], name='备用渠道B', api_key='')
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(agent_settings.resolve_config(b['id'])['api_key'], 'channel-b-offline-secret')
        self.assertEqual(agent_settings.resolve_config(b['id'])['config_revision'], 1)
        self.assertEqual(agent_settings.resolve_config(b['id'])['model'], 'model-b')
        cleared = await self.edit_channel(b['id'], clear_api_key=True)
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(agent_settings.resolve_config(b['id'])['api_key'], '')
        self.assertEqual(agent_settings.resolve_config(b['id'])['config_revision'], 2)
        self.assertEqual(agent_settings.resolve_config()['api_key'], 'channel-a-offline-secret')
        for response in (renamed, cleared):
            self.assertNotIn('channel-a-offline-secret', response.text)
            self.assertNotIn('channel-b-offline-secret', response.text)
        if os.name == 'nt':
            self.assertNotIn('channel-a-offline-secret', self.path.read_text(encoding='utf-8'))

    async def test_delete_active_disables_instead_of_selecting_other_or_environment(self):
        a = await self.add_channel()
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        await self.activate(a['id'])
        revision = (await self.library())['revision']
        response = await self.client.request('DELETE', f"/agent-settings/channels/{a['id']}?expected_revision={revision}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['active_channel_id'])
        self.assertEqual([item['id'] for item in response.json()['channels']], [b['id']])
        with patch.dict(os.environ, {'AGENT_API_KEY': 'env-secret', 'AGENT_BASE_URL': 'https://env.example', 'AGENT_MODEL': 'env-model'}):
            self.assertFalse(agent_settings.public_config()['configured'])
            self.assertEqual(agent_settings.resolve_config()['source'], 'local')

    async def test_stale_library_mutations_are_rejected_without_writing(self):
        a = await self.add_channel()
        before = self.path.read_bytes()
        requests = [('PUT', '/agent-settings/channels/' + a['id'], {'name': 'stale', 'expected_revision': 0}),
                    ('POST', '/agent-settings/channels/' + a['id'] + '/activate', {'expected_revision': 0}),
                    ('DELETE', '/agent-settings/channels/' + a['id'] + '?expected_revision=0', None)]
        for method, path, body in requests:
            response = await self.client.request(method, path, json_body=body)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.path.read_bytes(), before)

    async def test_channel_test_uses_only_its_own_key_and_never_saves_or_activates(self):
        a = await self.add_channel()
        await self.activate(a['id'])
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        before, seen = self.path.read_bytes(), []
        class Provider:
            def __init__(self, config):
                self.config = dict(config)
            async def __call__(self, role, instructions, context):
                seen.append((self.config, context))
                return {'summary': '离线连接成功', 'findings': []}
            async def close(self):
                pass
        with patch.object(agent_engine, '_SDKProvider', Provider):
            response = await self.client.post('/agent-settings/channels/' + b['id'] + '/test', json={'api_key': ''})
            self.assertEqual(response.status_code, 200, response.text)
            draft = await self.client.post('/agent-settings/channels/test', json={'base_url': 'https://new.example/v1', 'model': 'new-model', 'api_key': ''})
            self.assertEqual(draft.status_code, 422)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0]['api_key'], 'channel-b-offline-secret')
        self.assertEqual(seen[0][1]['manuscript']['content'], '连接测试，无论文正文。')

    async def test_origin_validation_and_secret_input_errors_remain_redacted(self):
        malformed = await self.client.post('/agent-settings/channels', json={'name': '渠道', 'api_key': {'value': 'never-echo-this-secret'}})
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn('never-echo-this-secret', malformed.text)
        denied = await self.client.request('POST', '/agent-settings/channels', json_body={'name': '渠道'}, headers={'Origin': 'https://other.example'})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(self.path.exists())

    async def test_unreadable_library_is_not_overwritten_or_replaced_by_environment(self):
        self.path.write_text('{"version":2,"channels":[],"revision":1,"active_channel_id":"missing"}', encoding='utf-8')
        before = self.path.read_bytes()
        self.assertFalse(agent_settings.public_config()['configured'])
        response = await self.client.post('/agent-settings/channels', json={'name': '不覆盖'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_unreadable_saved_credential_cannot_leak_or_start_connection_test(self):
        a = await self.add_channel()
        library = json.loads(self.path.read_text(encoding='utf-8'))
        library['channels'][0]['credential'] = {'encoding': 'unsupported-private-format', 'value': 'never-return-private-value'}
        self.path.write_text(json.dumps(library), encoding='utf-8')
        before = self.path.read_bytes()
        with patch.object(agent_engine, '_SDKProvider') as provider:
            response = await self.client.post('/agent-settings/channels/' + a['id'] + '/test', json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn('never-return-private-value', response.text)
        provider.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)


class ChannelReviewTests(ChannelHelpers, unittest.IsolatedAsyncioTestCase):
    provider = api_tests.AgentAPITests.provider
    finish = api_tests.AgentAPITests.finish
    create_run = api_tests.AgentAPITests.create_run
    guard = api_tests.AgentAPITests.guard
    create_review = api_tests.AgentAPITests.create_review

    async def asyncSetUp(self):
        await api_tests.AgentAPITests.asyncSetUp(self)
        self.settings_file = Path(self.temp.name) / 'agent-settings.json'
        self.env = patch.dict(os.environ, {'THESISDEBATE_AGENT_SETTINGS_PATH': str(self.settings_file),
            'AGENT_BASE_URL': '', 'AGENT_MODEL': '', 'AGENT_API_KEY': '',
            'OPENAI_BASE_URL': '', 'OPENAI_MODEL': '', 'OPENAI_API_KEY': ''})
        self.env.start()

    async def asyncTearDown(self):
        try:
            await api_tests.AgentAPITests.asyncTearDown(self)
        finally:
            self.env.stop()

    async def test_queued_review_captures_channel_before_active_switch(self):
        a = await self.add_channel()
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        await self.activate(a['id'])
        run = await self.create_run()
        schedule, captured = agent_api.schedule, []
        with patch.object(agent_api, 'schedule', side_effect=lambda *args, **kwargs: captured.append((args, kwargs))):
            review, _ = await self.create_review(run, finish=False)
        await self.activate(b['id'])
        self.assertEqual(captured[0][1]['config_snapshot']['api_key'], 'channel-a-offline-secret')
        schedule(*captured[0][0], **captured[0][1])
        await self.finish()
        result = self.store.get_review(review['id'])
        self.assertEqual(result['channel_id'], a['id'])
        self.assertEqual(result['model'], 'model-a')
        self.assertNotIn('channel-a-offline-secret', json.dumps(result))

    async def test_running_review_stays_on_original_and_new_task_uses_new_channel(self):
        a = await self.add_channel()
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        await self.activate(a['id'])
        started, release, seen = asyncio.Event(), asyncio.Event(), []
        class Provider:
            def __init__(self, config):
                self.config = dict(config)
            async def __call__(self, role, instructions, context):
                seen.append((self.config['channel_id'], self.config['api_key']))
                if self.config['channel_id'] == a['id']:
                    started.set()
                    await release.wait()
                return {'summary': '完成', 'assessments': [], 'challenges': []} if role == 'judge' else {'summary': '完成', 'findings': []}
            async def close(self):
                pass
        agent_api.PROVIDER_OVERRIDE = None
        with patch.object(agent_engine, '_SDKProvider', Provider):
            first_run = await self.create_run()
            first, _ = await self.create_review(first_run, finish=False)
            await asyncio.wait_for(started.wait(), 5)
            await self.activate(b['id'])
            release.set()
            await self.finish()
            first_calls = list(seen)
            second_run = await self.create_run('另一个文档。')
            second, _ = await self.create_review(second_run)
        self.assertTrue(first_calls)
        self.assertTrue(all(item == (a['id'], 'channel-a-offline-secret') for item in first_calls))
        self.assertTrue(all(item == (b['id'], 'channel-b-offline-secret') for item in seen[len(first_calls):]))
        self.assertEqual(self.store.get_review(first['id'])['channel_id'], a['id'])
        self.assertEqual(second['channel_id'], b['id'])

    async def _partial(self):
        async def failing(role, instructions, context):
            if role == 'methods':
                raise ConnectionError('offline interruption')
            return await self.provider(role, instructions, context)
        agent_api.PROVIDER_OVERRIDE = failing
        run = await self.create_run()
        review, guard = await self.create_review(run)
        self.assertEqual(review['status'], 'partial')
        return review, guard

    async def test_retry_uses_original_channel_while_another_is_active(self):
        a = await self.add_channel()
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        await self.activate(a['id'])
        review, guard = await self._partial()
        await self.activate(b['id'])
        agent_api.PROVIDER_OVERRIDE = self.provider
        response = await self.client.post('/agent-reviews/' + review['id'] + '/retry', json=guard)
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        result = self.store.get_review(review['id'])
        self.assertEqual(result['channel_id'], a['id'])
        self.assertEqual(result['model'], 'model-a')
        self.assertEqual(result['coverage']['roles']['innovation']['reused_windows'], [0])

    async def test_changed_deleted_or_legacy_channel_binding_rejects_retry(self):
        a = await self.add_channel()
        await self.activate(a['id'])
        review, guard = await self._partial()
        response = await self.edit_channel(a['id'], api_key='rotated-channel-secret')
        self.assertEqual(response.status_code, 200)
        before = self.store.get_review(review['id'])
        denied = await self.client.post('/agent-reviews/' + review['id'] + '/retry', json=guard)
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(self.store.get_review(review['id']), before)
        await self.client.request('DELETE', '/agent-settings/channels/' + a['id'])
        denied = await self.client.post('/agent-reviews/' + review['id'] + '/retry', json=guard)
        self.assertEqual(denied.status_code, 409)
        for key in ('channel_id', 'channel_identity', 'channel_config_revision'):
            before.pop(key, None)
        self.store.put_review(before)
        denied = await self.client.post('/agent-reviews/' + review['id'] + '/retry', json=guard)
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(agent_api.TASKS, {})

    async def test_cross_channel_engine_resume_does_not_reuse_windows(self):
        a = await self.add_channel()
        b = await self.add_channel('渠道B', 'channel-b-offline-secret', 'model-b')
        data = {'id': 'offline-cache', 'source': '原始正文。', 'preview': '修订正文。', 'materials': []}
        data.update(source_hash=api_tests.digest(data['source']), preview_hash=api_tests.digest(data['preview']))
        async def emit(*args):
            pass
        initial = await agent_engine.execute_review(data, emit, asyncio.Event(), provider=self.provider,
            config_snapshot=agent_settings.resolve_config(a['id']))
        self.assertEqual(initial['status'], 'completed')
        self.assertEqual(len(self.calls), 5)
        self.calls.clear()
        resumed = await agent_engine.execute_review(data, emit, asyncio.Event(), provider=self.provider,
            resume_results=initial['role_results'], config_snapshot=agent_settings.resolve_config(b['id']))
        self.assertEqual(len(self.calls), 5)
        self.assertTrue(all(not value.get('reused_windows') for value in resumed['coverage']['roles'].values()))


if __name__ == '__main__':
    unittest.main()
