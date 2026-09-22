"""Pipeline monitoring must distinguish unknown, missing and stale processing evidence."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jobhunter.operations.pipeline import it, summary
from jobhunter.workspace import Archive, identity, settings
from jobhunter.evaluation import remote_llm


class PipelineTests(unittest.TestCase):
    """Verify coverage on disposable data without triggering the monitored stages."""

    def save_current_remote(self, archive, oid, result):
        """Save one remote result with the same cache identity used by the production reader."""
        cfg = json.loads((remote_llm.ROOT / 'config/remote_llm.json').read_text(encoding='utf-8'))
        job = json.loads(archive.db.execute('SELECT data FROM opportunities WHERE id=?', (oid,)).fetchone()[0])
        payload = remote_llm.inputs(archive, 'selection', oid, cfg, job=job, cache={})
        prompt = (remote_llm.ROOT / cfg['prompts']['selection']).read_text(encoding='utf-8')
        key = remote_llm.digest({'source': payload, 'prompt': prompt,
                                 'settings': remote_llm.signature(cfg, 'selection')})
        archive.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                           ('remote:selection', oid, 'source', key,
                            json.dumps({'result': result}), 'test', '2026-01-01'))

    def test_funnel_partitions_both_axes_and_composes_the_tier(self):
        """Ogni asse è una partizione completa; il tier ne discende e non è mai salvato."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory)/'a.db')) as a:
            a.ingest([{'company_name': company, 'title': title, 'description': 'Build models',
                       'source_url': f'https://example.org/{i}'} for i, (company, title) in enumerate([
                           ('Mixed', 'Data Scientist'), ('Mixed', 'ML Engineer'),
                           ('Mixed', 'HR Manager'), ('Excluded', 'HR Manager')])], 'test')
            a.evaluations()
            rows = a.db.execute('SELECT id,data FROM opportunities').fetchall()
            with a.db:
                for row in rows:
                    title = json.loads(row['data'])['title']
                    if title == 'ML Engineer':
                        continue
                    stored = json.dumps({'result': {'decision': 'keep'}})
                    a.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                                 ('remote:selection', row['id'], 'hash', 'key', stored, 'test', '2026-01-01'))
            before = a.db.total_changes
            f = summary(a, settings(), Path(directory))['funnel']
            self.assertEqual(a.db.total_changes, before)
            self.assertEqual(f['archive'], {'jobs': 4, 'companies': 2})
            # I due assi sono partizioni complete dei rispettivi insiemi.
            self.assertEqual(sum(f['ruolo'].values()), 4)
            self.assertEqual(sum(f['azienda'].values()), 2)
            self.assertEqual(sum(f['tier'].values()), 2)
            self.assertEqual((f['ruolo']['tieni'], f['ruolo']['scarta']), (2, 2))
            # Nessuna azienda ha una categoria: l'asse azienda non è valutabile, ma i ruoli primari valgono da soli.
            self.assertEqual(f['azienda']['evidenza_mancante'], 2)
            self.assertEqual((f['tier']['B-esperienza'], f['tier']['evidenza-mancante']), (1, 1))
            # Il regex ha già deciso ogni ruolo: nessun giudizio remoto entra nella cascata.
            self.assertEqual(f['giudici'], {'regex': 4})
            # Il denominatore non cambia mai fra le tappe: ogni quota si legge contro lo stesso totale.
            for stage in f['percorso']:
                expected = 2 if stage.get('unit_change') else 4
                self.assertEqual(stage['base'], expected, stage['title'])
                self.assertEqual(sum(value for _, _, value in stage['parts']), expected, stage['title'])
            # Chi e' uscito non rientra: la fascia scura puo' solo crescere lungo le tappe sugli annunci.
            usciti = [sum(v for c, _, v in stage['parts'] if c == 'seg-gone')
                      for stage in f['percorso'] if not stage.get('unit_change')]
            self.assertEqual(usciti, sorted(usciti))

    def test_every_card_declares_the_population_it_received(self):
        """Un totale che nasce da una sottrazione va spiegato, e la barra va disegnata sulla sua base."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory)/'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': title, 'description': text,
                       'source_url': f'https://example.org/{i}'} for i, (title, text) in enumerate([
                           ('Data Scientist', 'Build models'), ('Growth Hacker', ''),
                           ('Growth Hacker', 'Grow the funnel'), ('HR Manager', 'Manage recruitment')])], 'test')
            a.evaluations()
            steps = {step['id']: step for step in summary(a, settings(), Path(directory))['steps']}
            # Ogni card dice da dove arriva; solo la prima non ha nulla a monte.
            for key, step in steps.items():
                self.assertEqual(bool(step['inflow']), key != 'collection', key)
            # La barra del giudice 2 sta sui «non so» del regex, non su una popolazione derivata in silenzio.
            regex = next(bar for bar in steps['filters']['extra'] if bar['title'] == 'Esito del regex')
            non_so = next(value for _, label, value in regex['parts'] if 'giudici successivi' in label)
            jev = steps['jev']
            self.assertEqual(jev['base'], non_so)
            self.assertEqual(sum(value for _, _, value in jev['parts']), non_so)
            # La copertura resta sulle chiamate possibili: una quota ferma non blocca lo stato per sempre.
            fermi = next(value for _, label, value in jev['parts'] if 'manca la descrizione' in label)
            self.assertEqual(jev['total'], non_so - fermi)
            # I numeri della frase hanno la stessa forma di quelli della barra, o non si agganciano a occhio.
            self.assertIn(it(non_so), jev['inflow'])
            self.assertIn(it(jev['total']), jev['inflow'])
            # L'ultimo passaggio non giudica: si misura sulle schede mancanti, non sui verdetti, e
            # la sua unita' non e' piu' la stessa della card precedente.
            remote = steps['remote']
            self.assertIsNone(remote['parts'])
            self.assertEqual(remote['base'], remote['total'])
            self.assertIn(it(remote['total']), remote['inflow'])
            aziendali = next(bar for bar in remote['extra'] if 'Schede aziendali' in bar['title'])
            self.assertEqual(sum(value for _, _, value in aziendali['parts']), aziendali['total'])
            self.assertEqual((it(23049), it(5908)), ('23.049', '5908'))
            # Dove cambia l'unita' di misura la card lo dichiara invece di lasciare il salto al lettore.
            self.assertIn('Cambia unità di misura', steps['descriptions']['inflow'])

    def test_remote_handoff_is_an_exact_partition_and_separates_review_from_new_work(self):
        """Review correnti, cache scadute e record mai valutati non finiscono nella stessa coda."""
        rows = [
            ('Data Scientist', 'Build models'),
            ('HR Manager', 'Manage recruitment'),
            ('Growth Hacker Keep', 'Plan growth experiments and analyse product metrics'),
            ('Growth Hacker Drop', 'Plan growth experiments and analyse product metrics'),
            ('Growth Hacker Review', 'Plan growth experiments and analyse product metrics'),
            ('Growth Hacker Ready', 'Plan growth experiments and analyse product metrics'),
            ('Growth Hacker Jev', 'Plan growth experiments and analyse product metrics'),
            ('Growth Hacker Blocked', ''),
            ('Growth Hacker Stale', 'Plan growth experiments and analyse product metrics'),
        ]
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as archive:
            archive.ingest([{'company_name': 'Example', 'title': title, 'description': description,
                             'source_url': f'https://example.org/{index}'}
                            for index, (title, description) in enumerate(rows)], 'test')
            archive.evaluations()
            ids = {json.loads(row['data'])['title']: row['id']
                   for row in archive.db.execute('SELECT id,data FROM opportunities')}
            self.save_current_remote(archive, ids['Growth Hacker Keep'], {'decision': 'keep'})
            self.save_current_remote(archive, ids['Growth Hacker Drop'], {'decision': 'exclude'})
            self.save_current_remote(archive, ids['Growth Hacker Review'], {'decision': 'review'})
            archive.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                               ('remote:selection', ids['Growth Hacker Stale'], 'source', 'old-key',
                                json.dumps({'result': {'decision': 'keep'}}), 'test', '2025-01-01'))
            # Deciso dal giudice System One: non deve comparire fra i candidati del remoto, o la
            # card di Qwen prometterebbe chiamate su annunci che non gli arriveranno mai.
            archive.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                               ('jev:selection', ids['Growth Hacker Jev'], 'source', 'key',
                                json.dumps({'result': {'decision': 'exclude', 'rationale': 'Test',
                                                       'evidence': ['Plan growth experiments and analyse product metrics']}}),
                                'jev-1.13.0', '2026-09-20'))
            handoff = summary(archive, settings(), Path(directory))['funnel']['handoff']
            self.assertEqual(sum(value for _, _, value in handoff['parts']), len(rows))
            self.assertEqual(handoff['counts'], {
                'regex_compatible': 1, 'regex_discarded': 1,
                'jev_compatible': 0, 'jev_discarded': 1,
                'remote_compatible': 1, 'remote_discarded': 1, 'remote_review': 1,
                'jev_review': 0, 'jev_ready': 1, 'blocked': 1, 'stale': 1,
            })

    def test_description_card_counts_company_coverage(self):
        """DESIGN §4: la copertura si misura sulle aziende, non sui ruoli sopravvissuti ai filtri."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory)/'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': title, 'description': text,
                       'source_url': f'https://example.org/{i}'} for i, (title,text) in enumerate([
                           ('Data Scientist', 'Build models'), ('Data Scientist', ''),
                           ('HR Manager', 'Manage recruitment')])], 'test')
            a.evaluations()
            result = summary(a, settings(), Path(directory))
            step = next(s for s in result['steps'] if s['id'] == 'descriptions')
            self.assertEqual((step['done'], step['total'], step['pending']), (1, 1, 0))
            self.assertEqual(result['opportunities'], 3)

    def test_empty_and_stale_evidence_is_read_only(self):
        """A changed description invalidates derived results without refreshing them on read."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as a:
            root = Path(directory)
            self.assertEqual(summary(a, settings(), root)['opportunities'], 0)
            a.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'location': 'Milano',
                       'description': 'Build models', 'source_url': 'https://example.org/1'}], 'test')
            a.search(eligibility='potential')
            job = a.db.execute('SELECT id,data FROM opportunities').fetchone()
            digest = identity(json.dumps('Build models', sort_keys=True, ensure_ascii=False))
            with a.db:
                a.db.execute('INSERT INTO enrichments VALUES(?,?,?,?,?,?,?)',
                             ('description', job['id'], digest, 'key', '{}', 'test', '2020-01-01T00:00:00+00:00'))
            with patch('jobhunter.evaluation.selection.evaluate', side_effect=AssertionError('Monitor must not evaluate')):
                before = a.db.total_changes
                result = summary(a, settings(), root)
                self.assertEqual(a.db.total_changes, before)
            steps = {s['id']: s for s in result['steps']}
            self.assertEqual(steps['filters']['done'], 1)
            self.assertIsNotNone(steps['filters']['updated_at'])
            for retired in ('categories', 'analytics', 'reparse', 'rewrite'):
                self.assertNotIn(retired, steps)
            with a.db:
                data = json.loads(job['data'])
                data['description'] = 'Changed content'
                a.db.execute('UPDATE opportunities SET data=?,content_hash=?', (json.dumps(data), 'changed'))
            steps = {s['id']: s for s in summary(a, settings(), root)['steps']}
            self.assertEqual(steps['filters']['stale'], 1)
            self.assertEqual(steps['filters']['done'], 0)

    def test_old_filter_cache_has_no_invented_date(self):
        """Historical statuses can be valid even when their calculation date was not stored."""
        with tempfile.TemporaryDirectory() as directory, closing(Archive(Path(directory) / 'a.db')) as a:
            a.ingest([{'company_name': 'Example', 'title': 'Data Scientist', 'source_url': 'https://example.org/1'}], 'test')
            a.search(eligibility='potential')
            with a.db:
                a.db.execute('DELETE FROM pipeline_updates')
            step = next(s for s in summary(a, settings(), Path(directory))['steps'] if s['id'] == 'filters')
            self.assertEqual(step['done'], 1)
            self.assertIsNone(step['updated_at'])
