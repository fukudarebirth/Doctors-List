import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doctors_list.storage import ROOT, digest, load_json, private_path, save_private_json


class StorageTests(unittest.TestCase):
    def test_private_json_roundtrip_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'data.json'
            expected = {'架空': ['test', 1]}
            result = save_private_json(path, expected)
            self.assertEqual(load_json(path), expected)
            self.assertEqual(result, {'verified': True, 'sha256': digest(expected)})
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_repository_output_rejected(self):
        with self.assertRaises(ValueError):
            save_private_json(ROOT / 'secrets.json', {})
        with self.assertRaises(ValueError):
            private_path(ROOT)

    def test_external_symlink_back_into_repo_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'link'
            try:
                link.symlink_to(ROOT, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('Symlink unavailable on this platform')
            with self.assertRaises(ValueError):
                private_path(link / 'secrets.json')

    def test_malformed_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            for text in ['{', '{"name":"a", "name":"b"}', 'this is not json']:
                path.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_json(path)

    def test_utf8_bom_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text('{"架空":true}', encoding='utf-8-sig')
            self.assertEqual(load_json(path), {'架空': True})

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text('{}', encoding='utf-8')
            with patch('doctors_list.storage.MAX_BYTES', 1):
                with self.assertRaises(ValueError):
                    load_json(path)

    def test_failed_atomic_replace_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            save_private_json(path, {'old': True})
            with patch('doctors_list.storage.os.replace', side_effect=OSError('simulated failure')) as replace:
                with self.assertRaises(OSError):
                    save_private_json(path, {'new': True})
                replace.assert_called_once()
            self.assertEqual(load_json(path), {'old': True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_readback_failure_never_reports_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            with patch('doctors_list.storage.load_json', return_value={'corrupt': True}):
                with self.assertRaises(OSError):
                    save_private_json(path, {'intended': True})
            self.assertFalse(path.exists())

    def test_content_digest_order_independent_not_timestamp_based(self):
        self.assertEqual(digest({'a': 1, 'b': 2}), digest({'b': 2, 'a': 1}))
        self.assertNotEqual(digest({'a': 1}), digest({'a': 2}))


if __name__ == '__main__':
    unittest.main()
