"""Progress and document-profile tests use injected providers; no paid model calls."""
import asyncio
import json
import unittest

from app import agent_engine as engine
from tests.test_agents import output, proposed, snapshot


class AgentProgressUpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, data=None, provider=None):
        events = []
        async def emit(kind, payload):
            events.append((kind, json.loads(json.dumps(payload))))
        async def default_provider(role, instructions, context):
            return output(role, context)
        result = await engine.execute_review(data or snapshot(), emit, asyncio.Event(), provider=provider or default_provider)
        return result, events

    async def test_expert_progress_counts_actual_completed_windows(self):
        async def provider(role, instructions, context):
            return output(role, context, findings=False)
        result, events = await self.execute(snapshot(preview='甲' * 9000, source='乙' * 9000), provider)
        self.assertEqual(result['status'], 'completed')
        for role in ('methods', 'innovation', 'norms', 'comparator'):
            records = [payload['progress'] for kind, payload in events if payload['agent_id'] == role and kind in ('role_started', 'role_checkpoint')]
            self.assertEqual([item['completed_units'] for item in records], [0, 1, 2, 3])
            self.assertTrue(all(item['total_units'] == 3 and item['unit'] == 'window' for item in records))
            self.assertTrue(all(item['updated_at'] for item in records))
            self.assertEqual(next(item for item in result['agents'] if item['id'] == role)['progress']['completed_units'], 3)

    async def test_judge_emits_each_real_batch(self):
        async def provider(role, instructions, context):
            value = output(role, context)
            if role != 'judge':
                value['findings'] = [{**proposed(role), 'title': role + str(index)} for index in range(6)]
            return value
        result, events = await self.execute(provider=provider)
        checkpoints = [payload for kind, payload in events if kind == 'role_checkpoint' and payload['agent_id'] == 'judge' and 'batch_index' in payload]
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(len(result['findings'][0]['contributions']), 24)
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual([item['progress']['completed_units'] for item in checkpoints], [1, 2])
        self.assertTrue(all(item['progress']['total_units'] == 2 and item['progress']['unit'] == 'batch' for item in checkpoints))

    async def test_general_profile_goal_and_confirmed_rules_reach_every_provider(self):
        data = snapshot()
        data.update(document_kind='lab_report', review_profile_id='general', user_goal='检查实验报告结构并改善表达', confirmed_template={'name': '课程实验报告', 'required_sections': ['实验目的', '实验结果']})
        seen = []
        async def provider(role, instructions, context):
            seen.append((role, instructions, context))
            return output(role, context)
        result, _ = await self.execute(data, provider)
        self.assertEqual(result['agents'][0]['name'], '结构完整性审查官')
        self.assertEqual(result['agents'][1]['name'], '表达一致性审查官')
        for role, instructions, context in seen:
            self.assertEqual(context['user_goal'], data['user_goal'])
            self.assertEqual(context['confirmed_template'], data['confirmed_template'])
            self.assertEqual(context['document_kind'], 'lab_report')
            self.assertFalse(context['modification_policy']['automatic_apply'])
        self.assertIn('不对通用文档强加研究方法', next(value for role, value, _ in seen if role == 'methods'))

    async def test_number_reference_formula_changes_need_manual_check(self):
        samples = [('样本10人', '样本20人'), ('引用[1]', '引用[2]'), ('$x+1$', '$x+2$'), ('来源https://a.example', '来源https://b.example')]
        for before, after in samples:
            self.assertTrue(engine.protected_content_changed(before, after))
            async def provider(role, instructions, context):
                value = output(role, context, findings=False)
                if role != 'judge':
                    value['findings'] = [proposed(role, quote=before, replacement=after)]
                return value
            result, _ = await self.execute(snapshot(preview=before, source='原稿'), provider)
            self.assertTrue(all(not item['applicable'] and item['requires_manual_review'] and item['risk_level'] == 'high' for item in result['findings']))
        self.assertFalse(engine.protected_content_changed('样本10人。', '样本10人，已记录。'))

    async def test_identical_comparison_has_zero_actual_units(self):
        result, events = await self.execute(snapshot(source='😀研究方法：样本10人。\n'))
        comparator = next(item for item in result['agents'] if item['id'] == 'comparator')
        self.assertEqual(comparator['progress']['total_units'], 0)
        self.assertEqual(comparator['progress']['completed_units'], 0)
        self.assertIn('无需模型调用', comparator['summary'])
        self.assertTrue(any(kind == 'role_completed' and item['agent_id'] == 'comparator' for kind, item in events))


if __name__ == '__main__':
    unittest.main()
