"""Linux accepted-runtime integration, isolated journals, no GPU or production writes."""
import contextlib
import copy
import json
import unittest

import ugs_idle_v2_entry as entry
import test_dispatch as fixtures
import dispatch_cadence as d


class RealJournalTests(fixtures.DispatchTests):
    def setUp(self):
        super().setUp()
        c = entry.core
        self.state.update(revision=1, schema_version=3)
        journal = self.c.ROOT / 'events.jsonl'
        c.append_json(journal, {'at': c.stamp(), 'event': 'fixture', 'state': self.state,
                               'sha256': c.digest(c.canonical(self.state))})
        store = c.Store(self.c.ROOT)
        @contextlib.contextmanager
        def tx(event=None):
            self.clock.sleep(self.replay_delay)
            with store.tx(event) as trial:
                yield trial
                if self.commit_fail and event == 'launch_intent':
                    raise OSError('commit failed')
            if event:
                self.state = store.read()
                self.events.append(event)
        self.c.Store = lambda: type('FixtureStore', (), {'tx': staticmethod(tx)})()
        # Real hash preflight reads only this temporary fixture file.
        source = self.c.ROOT / 'worker.py'
        source.write_text('fixture only\n')
        stage = self.state['jobs'][0]['manifest']['stages'][0]
        stage.update(source_sha256={str(source): c.hash_file(source)}, result_path=str(self.c.ROOT / 'result.json'))
        self.job = copy.deepcopy(self.state['jobs'][0])
        with store.tx('fixture_manifest') as s:
            s['jobs'] = copy.deepcopy(self.state['jobs'])
        self.state = store.read()

    def call(self):
        # Persist fixture-side competing updates before the real transaction,
        # so race tests exercise the on-disk state, not a stale Python copy.
        store = entry.core.Store(self.c.ROOT)
        current = store.read()
        if self.state != current:
            desired = copy.deepcopy(self.state)
            with store.tx('fixture_competing_update') as s:
                revision = s['revision']
                s.clear()
                s.update(desired)
                s['revision'] = revision
            self.state = store.read()
        return super().call()

    def test_real_journal_corruption_rejected_before_observation(self):
        p = self.c.ROOT / 'events.jsonl'
        with p.open('ab') as f:
            f.write(b'{"incomplete":')
        with self.assertRaisesRegex(ValueError, 'Incomplete journal'):
            self.call()
        self.assertFalse(self.scans or self.spawns)

    def test_real_source_hash_refuses_modified_source(self):
        self.c.source_preflight = entry.core.source_preflight
        (self.c.ROOT / 'worker.py').write_text('changed\n')
        self.failed()
        self.assertEqual(self.state['jobs'][0]['status'], 'BLOCKED')
        self.assertFalse(self.spawns)

    def test_current_entry_preserves_memory_idle_and_resource_extensions(self):
        c = entry.core
        self.assertTrue(c._dispatch_v1 and c._resource_override_v1 and c._idle_window_v2)
        self.assertTrue(callable(c.memory_matches))
        self.assertIn('ugs_idle_v2_entry.py', str(c.SELF))


if __name__ == '__main__':
    unittest.main()
