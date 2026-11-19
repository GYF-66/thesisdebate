from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app import main, agent_api
from app.storage import ReviewStore
from tests.test_agent_api import ASGIClient


class BatchRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = ReviewStore(Path(self.temp.name) / 'db.sqlite3')
        self.saved = agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES
        agent_api.STORE = self.store
        main.RUNS, main.CHANGES, main.SOURCES = {}, {}, {}
        self.client = ASGIClient(main.app)
        response = await self.client.post('/format-runs', json={'profile_id':'preset-generic','document_type':'markdown','content':'# 标题\n\n正文后有空格。  \n\n[1] 参考文献'})
        self.run = response.json()
        self.assertEqual(response.status_code, 200, response.text)

    async def asyncTearDown(self):
        self.store.close()
        agent_api.STORE, main.RUNS, main.CHANGES, main.SOURCES = self.saved
        self.temp.cleanup()

    async def guard(self):
        response = await self.client.get('/format-runs/' + self.run['id'] + '/review')
        self.assertEqual(response.status_code, 200, response.text)
        review = response.json()
        return {key:review[value] for key,value in [('expected_source_hash','source_hash'),('expected_preview_hash','preview_hash')]}

    async def create_run(self, source, rules):
        response = await self.client.post('/format-runs', json={
            'profile_id': 'preset-generic', 'document_type': 'markdown',
            'content': source, 'requested_rules': rules,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.run = response.json()
        return [item for item in main.CHANGES.values() if item.run_id == self.run['id']]

    def duplicate_change(self, change, **updates):
        duplicate = change.model_copy(update={
            'id': 'second-source-' + change.id,
            'explanation': '另一独立来源提出的格式建议。', **updates,
        })
        main.CHANGES[duplicate.id] = duplicate
        run = main.RUNS[self.run['id']]
        main.RUNS[run.id] = run.model_copy(update={'changes_count': run.changes_count + 1})
        self.store.save_format_state(
            {key: item.model_dump(mode='json') for key, item in main.RUNS.items()},
            {key: item.model_dump(mode='json') for key, item in main.CHANGES.items()},
            dict(main.SOURCES),
        )
        return duplicate

    async def snapshot(self):
        response = await self.client.get('/format-runs/' + self.run['id'] + '/batches')
        self.assertEqual(response.status_code, 200, response.text)
        return {
            'head': self.store.get_head(self.run['document_id']),
            'versions': self.store.list_versions(self.run['document_id']),
            'durable_state': self.store.load_format_state(),
            'run': main.RUNS[self.run['id']].model_dump(mode='json'),
            'changes': {key: item.model_dump(mode='json') for key, item in main.CHANGES.items()},
            'sources': dict(main.SOURCES), 'batches': response.json(),
        }

    async def decide_batch(self, decision, change_ids=None, batch_id=None):
        return await self.client.post('/format-runs/' + self.run['id'] + '/batch-decisions', json={
            'decision': decision, 'change_ids': change_ids or [],
            'batch_id': batch_id, **await self.guard(),
        })

    def assert_changes_restored(self, changes):
        durable = self.store.load_format_state()['changes']
        for change in changes:
            self.assertEqual(main.CHANGES[change.id].model_dump(mode='json'), change.model_dump(mode='json'))
            self.assertEqual(durable[change.id], change.model_dump(mode='json'))
        self.assertEqual(main.RUNS[self.run['id']].accepted_count, 0)
        self.assertEqual(main.RUNS[self.run['id']].rejected_count, 0)

    async def test_batch_accept_recheck_and_exact_undo(self):
        changes = [item for item in main.CHANGES.values() if item.rule_id == 'paragraph.spacing']
        self.assertTrue(changes)
        old = self.store.get_head(self.run['document_id'])['content']
        response = await self.client.post('/format-runs/' + self.run['id'] + '/batch-decisions', json={'change_ids':[item.id for item in changes], **await self.guard()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('recheck', response.json())
        self.assertNotEqual(self.store.get_head(self.run['document_id'])['content'], old)
        response = await self.client.post('/format-runs/' + self.run['id'] + '/batch-decisions', json={'decision':'undo','batch_id':response.json()['batch_id'],**await self.guard()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get_head(self.run['document_id'])['content'], old)
        self.assertTrue(all(main.CHANGES[item.id].status == 'pending' for item in changes))

    async def test_batch_rejects_stale_or_nonformat_items_without_partial_commit(self):
        ids = [item.id for item in main.CHANGES.values()]
        before = self.store.get_head(self.run['document_id'])
        response = await self.client.post('/format-runs/' + self.run['id'] + '/batch-decisions', json={'change_ids':ids,'expected_source_hash':'0'*64,'expected_preview_hash':'0'*64})
        self.assertEqual(response.status_code, 409)
        response = await self.client.post('/format-runs/' + self.run['id'] + '/batch-decisions', json={'change_ids':ids + ['missing'],**await self.guard()})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get_head(self.run['document_id']), before)
        self.assertTrue(all(item.status == 'pending' for item in main.CHANGES.values()))

    async def test_batch_reject_and_undo_restore_decisions_without_creating_versions(self):
        changes = await self.create_run('# 标题\n正文有尾空格。  \n', ['heading.spacing', 'paragraph.spacing'])
        self.assertGreaterEqual(len(changes), 2)
        before = await self.snapshot()
        response = await self.decide_batch('reject', [item.id for item in changes])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['decision'], 'reject')
        self.assertEqual(result['count'], len(changes))
        self.assertEqual(result['recheck']['accepted_count'], 0)
        self.assertEqual(result['recheck']['rejected_count'], len(changes))
        self.assertEqual(result['recheck']['pending_count'], 0)
        self.assertEqual(result['recheck']['status'], 'passed')
        rejected = await self.snapshot()
        self.assertEqual(rejected['head'], before['head'])
        self.assertEqual(rejected['versions'], before['versions'])
        self.assertTrue(all(main.CHANGES[item.id].status == 'rejected' for item in changes))
        self.assertTrue(all(rejected['durable_state']['changes'][item.id]['status'] == 'rejected' for item in changes))
        self.assertEqual(rejected['batches'], [{
            'id': result['batch_id'], 'count': len(changes), 'undone': False, 'can_undo': True,
        }])

        response = await self.decide_batch('undo', batch_id=result['batch_id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_changes_restored(changes)
        restored = await self.snapshot()
        self.assertEqual(restored['head'], before['head'])
        self.assertEqual(restored['versions'], before['versions'])
        self.assertEqual(restored['batches'], [{
            'id': result['batch_id'], 'count': len(changes), 'undone': True, 'can_undo': False,
        }])
        response = await self.decide_batch('undo', batch_id=result['batch_id'])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.snapshot(), restored)

    async def test_identical_edits_from_two_sources_apply_once_and_undo_together(self):
        source = '# 标题\n正文保留 42 $x^2$ [2]。\n'
        changes = await self.create_run(source, ['heading.spacing'])
        self.assertEqual(len(changes), 1)
        original = changes[0]
        duplicate = self.duplicate_change(original)
        before = await self.snapshot()
        response = await self.decide_batch('accept', [original.id, duplicate.id, original.id])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['recheck']['accepted_count'], 2)
        self.assertEqual(result['recheck']['applied_count'], 2)
        self.assertEqual(result['recheck']['pending_count'], 0)
        self.assertEqual(result['recheck']['blocked_change_ids'], [])
        integrity = next(item for item in result['recheck']['checks'] if item['id'] == 'patch.integrity')
        self.assertEqual(integrity['status'], 'passed', integrity['detail'])
        self.assertEqual(result['recheck']['status'], 'passed')
        preview = await self.client.get('/format-runs/' + self.run['id'] + '/preview')
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['preview'], '# 标题\n\n正文保留 42 $x^2$ [2]。\n')
        self.assertEqual(self.store.get_head(self.run['document_id'])['content'], preview.json()['preview'])
        self.assertEqual(set(preview.json()['accepted_change_ids']), {original.id, duplicate.id})
        self.assertEqual(set(preview.json()['applied_change_ids']), {original.id, duplicate.id})
        self.assertEqual(main.SOURCES[self.run['id']], source)
        self.assertEqual(len(self.store.list_versions(self.run['document_id'])), len(before['versions']) + 1)
        durable = self.store.load_format_state()['changes']
        self.assertTrue(all(durable[item.id]['status'] == 'accepted' for item in [original, duplicate]))

        response = await self.decide_batch('undo', batch_id=result['batch_id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get_head(self.run['document_id'])['content'], source)
        self.assert_changes_restored([original, duplicate])
        self.assertEqual(len(self.store.list_versions(self.run['document_id'])), len(before['versions']) + 2)

    async def test_different_replacements_conflict_without_any_partial_commit(self):
        changes = await self.create_run('# 标题\n正文有尾空格。  \n', ['heading.spacing', 'paragraph.spacing'])
        spacing = next(item for item in changes if item.rule_id == 'heading.spacing')
        independent = next(item for item in changes if item.rule_id == 'paragraph.spacing')
        conflicting = self.duplicate_change(spacing, replacement='\n\n', after='新增两个空行')
        before = await self.snapshot()
        response = await self.decide_batch('accept', [independent.id, spacing.id, conflicting.id])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('范围冲突', response.json()['detail'])
        self.assertEqual(await self.snapshot(), before)
        self.assertTrue(all(main.CHANGES[item.id].status == 'pending' for item in [independent, spacing, conflicting]))

    async def test_more_than_100_heading_spacing_changes_accept_and_undo_exactly(self):
        sections = [f'# 第{index}节\n正文 {index} 保留 $x_{index}$ [2]。' for index in range(1, 121)]
        source = '\n'.join(sections) + '\n'
        expected = '\n\n'.join(section.replace('\n', '\n\n', 1) for section in sections) + '\n'
        changes = await self.create_run(source, ['heading.spacing'])
        self.assertEqual(len(changes), 239)
        self.assertTrue(all(item.rule_id == 'heading.spacing' for item in changes))
        before = await self.snapshot()
        response = await self.decide_batch('accept', [item.id for item in changes])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['count'], len(changes))
        self.assertEqual(result['recheck']['status'], 'passed')
        self.assertEqual(result['recheck']['applied_count'], len(changes))
        self.assertEqual(result['recheck']['accepted_count'], len(changes))
        self.assertEqual(result['recheck']['pending_count'], 0)
        self.assertEqual(self.store.get_head(self.run['document_id'])['content'], expected)
        self.assertTrue(all(main.CHANGES[item.id].status == 'accepted' for item in changes))
        accepted = await self.snapshot()
        self.assertEqual(len(accepted['versions']), len(before['versions']) + 1)
        self.assertEqual(accepted['batches'], [{
            'id': result['batch_id'], 'count': len(changes), 'undone': False, 'can_undo': True,
        }])

        response = await self.decide_batch('undo', batch_id=result['batch_id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['recheck']['applied_count'], 0)
        self.assertEqual(response.json()['recheck']['pending_count'], len(changes))
        self.assertEqual(self.store.get_head(self.run['document_id'])['content'], source)
        self.assert_changes_restored(changes)
        self.assertEqual(len(self.store.list_versions(self.run['document_id'])), len(before['versions']) + 2)

    async def test_generic_profile_and_template_snapshot_are_not_thesis(self):
        self.assertEqual(self.run['document_kind'], 'generic')
        self.assertEqual(self.run['review_profile_id'], 'general')
        self.assertEqual(self.run['template_snapshot']['required_sections'], [])

    async def test_noop_commit_still_checks_head_inside_transaction(self):
        before = self.store.get_head(self.run['document_id'])
        with self.assertRaisesRegex(ValueError, 'head conflict'):
            self.store.commit_format_state(
                {key:item.model_dump(mode='json') for key,item in main.RUNS.items()},
                {key:item.model_dump(mode='json') for key,item in main.CHANGES.items()}, dict(main.SOURCES),
                self.run['id'], self.run['document_id'], before['content'], 'test_noop',
                expected_head_hash='0'*64, create_version=False,
            )
        self.assertEqual(self.store.get_head(self.run['document_id']), before)
