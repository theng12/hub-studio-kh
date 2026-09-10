"""Keep the model smoke aligned with the existing public job contract."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
import smoke_voice


class ModelSmokeContractTests(unittest.TestCase):
    def test_pilot_inputs_require_exact_refs_and_file_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = 'a' * 40
            model = root / 'hub/models--example--voice'
            snapshot = model / 'snapshots' / revision
            snapshot.mkdir(parents=True)
            (model / 'refs').mkdir()
            (snapshot / 'tokenizer.json').write_bytes(b'asset')
            expected = {'models': [{'repo': 'example/voice', 'revision': revision,
                'files': [{'path': 'tokenizer.json', 'bytes': 5,
                           'sha256': hashlib.sha256(b'asset').hexdigest()}]}]}
            reference = model / 'refs/main'
            reference.write_text(revision + '\n')
            with self.assertRaisesRegex(ValueError, 'reference'):
                smoke_voice.validate_pilot_inputs(root, expected)
            reference.write_text(revision)
            smoke_voice.validate_pilot_inputs(root, expected)
            (snapshot / 'tokenizer.json').write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                smoke_voice.validate_pilot_inputs(root, expected)

    def test_public_job_id_is_used_for_poll_audio_and_transcription(self):
        audio = b'RIFF' + b'\0' * 100
        responses = [
            (200, {'job': {'id': 'public-id', 'state': 'running'}}),
            (200, {'job': {'id': 'public-id', 'state': 'done'}}),
        ]
        audio_response = io.BytesIO(audio)
        audio_response.headers = {'X-Content-SHA256': hashlib.sha256(audio).hexdigest()}
        transcript_response = io.BytesIO(json.dumps({'text': 'Standalone test.'}).encode())
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(smoke_voice, 'request', side_effect=responses) as request, \
                 patch.object(smoke_voice.time, 'sleep'), \
                 patch.object(smoke_voice.urllib.request, 'urlopen', side_effect=[audio_response, transcript_response]) as opened:
                result = smoke_voice.exercise_models('http://127.0.0.1:1234', 'test-token', Path(temporary))
                self.assertTrue(request.call_args.args[0].endswith('/jobs/public-id'))
                self.assertTrue(opened.call_args_list[0].args[0].endswith('/jobs/public-id/audio'))
                self.assertIn(b'job_id=public-id', opened.call_args_list[1].args[0].data)
                self.assertEqual(result['audio_sha256'], hashlib.sha256(audio).hexdigest())


if __name__ == '__main__':
    unittest.main()
