"""Streamlit reruns, source/provenance state and all AI actions with fake APIs."""
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def paper(n):
    return {'reference_number': n, 'title': f'Battery degradation research topic {n}',
            'original_text': f'Author. Battery degradation research topic {n}. 2024.',
            'doi': None, 'year': '2024', 'metadata_sources': [], 'source_links': [],
            'oa_pdf_url': None, 'access_status': 'scholar_search', 'citation_contexts': [],
            'citation_mentions': 0}


class Upload:
    file_id = 'fake-upload-1'
    name = 'paper.pdf'
    def getvalue(self):
        return b'%PDF-1.4 fake-test'


def button(app, label):
    return next(b for b in app.button if b.label == label)


class StreamlitAppTests(unittest.TestCase):
    def test_pdf_datasets_deep_search_and_ai_tools(self):
        calls = []
        def fake_api(path, **kwargs):
            calls.append((path, kwargs))
            if path == '/api/references':
                return {'filename': 'paper.pdf', 'results': [paper(1), paper(2)], 'total_references': 2}
            if path == '/api/ai/extract-references':
                kwargs['on_event']({'type': 'progress', 'completed': 1, 'total': 1})
                return {'filename': 'paper.pdf', 'results': [paper(1), paper(2), paper(3)], 'total_references': 3}
            if path == '/api/open-access/search':
                for item in kwargs['json_body']['papers']:
                    result = {'reference_number': item['reference_number'], 'pdf_locations': [
                        {'url': f"https://repo.example/{item['reference_number']}.pdf", 'source': 'Unpaywall', 'version': 'acceptedVersion'}],
                        'metadata_sources': ['Unpaywall'], 'source_links': [], 'oa_search': {'CORE': 'requires CORE_API_KEY'}}
                    kwargs['on_event']({'type': 'item', 'result': result, 'completed': item['reference_number'], 'total': 3})
                return {'total': 3}
            if path == '/api/summarize': return {'summary': 'Mock paper summary'}
            if path == '/api/ai/intents':
                return {'intents': [{'id': n, 'intent': 'Background', 'reason': 'Mock reason'} for n in (1, 2, 3)], 'contexts': {}}
            if path == '/api/ai/clusters':
                return {'clusters': [{'name': f'Theme {n}', 'ids': [n]} for n in (1, 2, 3)]}
            if path == '/api/ai/synthesis': return {'synthesis': 'Mock research gap'}
            if path == '/api/ai/qa': return {'answer': 'Mock grounded answer'}
            raise AssertionError(path)

        with patch('streamlit.file_uploader', return_value=Upload()), patch('streamlit_backend.call_api', side_effect=fake_api):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=10).run()
            button(app, '⌘ สกัดด้วยโค้ด').click().run()
            self.assertEqual(len(app.session_state['datasets']['code']['payload']['results']), 2)
            app.text_input(key='ai_key').set_value('fake-session-key').run()
            button(app, '✦ สกัดบรรณานุกรมด้วย AI').click().run()
            self.assertEqual(set(app.session_state['datasets']), {'code', 'ai'})
            self.assertEqual(app.selectbox(key='dataset_picker').value, 'ai')
            app.multiselect[0].set_value([1, 2, 3]).run()
            button(app, 'วิเคราะห์ citation intent').click().run()
            button(app, 'จัดธีมบนกราฟ').click().run()
            button(app, 'สังเคราะห์ 3–5 เปเปอร์').click().run()
            self.assertEqual(app.session_state['datasets']['ai']['analysis'], 'Mock research gap')
            button(app, 'สรุปงานวิจัยต้นทาง').click().run()
            self.assertEqual(app.session_state['summary'], 'Mock paper summary')
            app.chat_input[0].set_value('What is the method?').run()
            self.assertEqual(app.session_state['datasets']['ai']['history'][-1]['content'], 'Mock grounded answer')
            button(app, 'ค้น PDF เพิ่มทุกแหล่ง').click().run()
            items = app.session_state['datasets']['ai']['payload']['results']
            self.assertTrue(all(p['oa_pdf_url'] and p['intent'] == 'Background' for p in items))
            self.assertEqual(app.multiselect[0].value, [1, 2, 3])
            self.assertEqual(len(app.session_state['datasets']['ai']['themes']), 3)
            self.assertIn('CORE', app.session_state['datasets']['ai']['oa_note'])
            app.selectbox(key='dataset_picker').set_value('code').run()
            self.assertEqual(len(app.session_state['datasets']['code']['payload']['results']), 2)
            button(app, 'ล้าง API key').click().run()
            self.assertEqual(app.session_state['ai_key'], '')
            self.assertFalse(app.exception, [e.message for e in app.exception])
        ai_calls = [kw for path, kw in calls if path.startswith('/api/ai/') or path == '/api/summarize']
        self.assertTrue(all(kw['headers']['X-AI-API-Key'] == 'fake-session-key' for kw in ai_calls))
        self.assertTrue(all(kw['files']['file'][0] == 'paper.pdf' for kw in ai_calls))

    def test_doi_import_and_session_key_isolation(self):
        def fake_api(path, **kwargs):
            self.assertEqual(path, '/api/doi')
            self.assertEqual(kwargs['data']['doi'], '10.1234/example')
            return {'mode': 'doi', 'filename': 'DOI paper', 'results': [paper(1)], 'total_references': 1}
        with patch('streamlit_backend.call_api', side_effect=fake_api):
            first = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=10).run()
            first.radio(key='source_mode').set_value('DOI').run()
            first.text_input(key='doi_input').set_value('10.1234/example').run()
            button(first, 'ค้นจาก DOI').click().run()
            first.text_input(key='ai_key').set_value('fake-first-session').run()
            second = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py'), default_timeout=10).run()
            self.assertEqual(second.session_state['ai_key'], '')
            self.assertEqual(second.session_state['datasets'], {})
            first.text_input(key='doi_input').set_value('10.1234/another').run()
            self.assertEqual(first.session_state['datasets'], {})
            self.assertFalse(first.exception, [e.message for e in first.exception])
