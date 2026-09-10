import asyncio
import base64
from dataclasses import replace
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from botocore.exceptions import ClientError
from google.genai import types

from app.services import artifact_service as artifacts
from app.services.supabase_storage import SupabaseStorageClient
from app.services.read_cache import RevisionCache


class NativeStorageTests(unittest.TestCase):
    def setUp(self):
        self.objects = {}
        self.signs = 0
        self.fail_delete = False
        def handle(request):
            path = request.url.path.removeprefix('/storage/v1/')
            self.assertEqual(request.headers['apikey'], 'sb_secret_test')
            self.assertNotIn('authorization', request.headers)
            if path.startswith('object/list-v2/'):
                body = json.loads(request.content)
                return httpx.Response(200, json={'hasNext': False, 'objects': [
                    {'name': k, 'updated_at': '2026-09-10T00:00:00Z'}
                    for k in self.objects if k.startswith(body['prefix'])]})
            if path.startswith('object/sign/'):
                self.signs += 1
                return httpx.Response(200, json={'signedURL': '/object/sign/b/file?token=fake'+str(self.signs)})
            if request.method == 'DELETE':
                if self.fail_delete:
                    return httpx.Response(403, json={'statusCode': '403'})
                for key in json.loads(request.content)['prefixes']:
                    self.objects.pop(key, None)
                return httpx.Response(200, json=[])
            if path.startswith('object/info/authenticated/'):
                key = path.removeprefix('object/info/authenticated/b/')
                obj = self.objects.get(key)
                if obj is None:
                    return httpx.Response(400, json={'statusCode': '404', 'error': 'not_found'})
                return httpx.Response(200, json={'metadata': obj['meta'], 'content_type': obj['type'],
                    'last_modified': '2026-09-10T00:00:00Z'})
            if path.startswith('object/authenticated/'):
                key = path.removeprefix('object/authenticated/b/')
                return httpx.Response(200, content=self.objects[key]['body'])
            key = path.removeprefix('object/b/')
            self.objects[key] = {'body': request.content, 'type': request.headers['content-type'],
                'meta': json.loads(base64.b64decode(request.headers.get('x-metadata', 'e30=')))}
            return httpx.Response(200, json={'Key': key})
        self.client = SupabaseStorageClient('https://example.supabase.co', 'sb_secret_test',
                                           transport=httpx.MockTransport(handle))
        with patch.object(artifacts, 'settings', replace(artifacts.settings,
                          supabase_storage_key='sb_secret_test', supabase_url='https://example.supabase.co')):
            self.service = artifacts.SupabaseArtifactService(bucket_name='b')
        self.service._client.http.close()
        self.service._client = self.client

    def tearDown(self):
        self.client.http.close()

    def test_text_binary_and_reference_roundtrip_with_versions(self):
        for part in [types.Part(text='Hello café'),
                     types.Part(inline_data=types.Blob(data=b'png', mime_type='image/png', display_name='Cover')),
                     types.Part(file_data=types.FileData(file_uri='https://example.com/ref', mime_type='text/plain'))]:
            v = self.service._save_artifact('app', 'u', 's', 'folder/test', part, {'label': 'é'})
            self.assertEqual(self.service._load_artifact('app', 'u', 's', 'folder/test', v), part)
            self.assertEqual(self.service._get_artifact_version_sync('app', 'u', 's', 'folder/test', v).custom_metadata['label'], 'é')
        self.assertEqual(self.service.latest_versions('app', 'u', 's'), {'folder/test': 2})
        self.assertEqual(self.service._list_versions('app', 'u', 's', 'folder/test'), [0, 1, 2])
        self.assertIsNone(self.service._load_artifact('app', 'u', 's', 'missing', 0))

    def test_stable_signed_urls_expiry_versions_and_delete_failure(self):
        self.service._save_artifact('app', 'u', 's', 'cover.png', types.Part(text='test'))
        args = dict(app_name='app', user_id='u', session_id='s', filename='cover.png', version=0, expires_in=100)
        with patch.object(artifacts.time, 'monotonic', return_value=0):
            first = self.service.public_url(**args)
        with patch.object(artifacts.time, 'monotonic', return_value=89):
            self.assertEqual(self.service.public_url(**args), first)
        with patch.object(artifacts.time, 'monotonic', return_value=91):
            self.assertNotEqual(self.service.public_url(**args), first)
        self.assertEqual(self.signs, 2)
        self.service.public_url(**{**args, 'version': 1})
        self.assertEqual(self.signs, 3)
        self.fail_delete = True
        with self.assertRaises(ClientError):
            self.service.delete_session_artifacts('app', 'u', 's')
        self.fail_delete = False
        self.assertEqual(self.service.delete_session_artifacts('app', 'u', 's'), 1)
        self.assertFalse(self.objects)

    def test_flat_listing_uses_continuation_cursor(self):
        requests = []
        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertFalse(body['with_delimiter'])
            last = body.get('cursor') == 'next'
            return httpx.Response(200, json={'hasNext': not last, 'nextCursor': 'next' if not last else None,
                'objects': [{'name': 'nested/file/'+str(int(last)), 'updated_at': '2026-09-10T00:00:00Z'}]})
        with httpx.Client(base_url='https://example.com/storage/v1/', transport=httpx.MockTransport(handler)) as http:
            old, self.client.http = self.client.http, http
            pages = list(self.client.paginate(Bucket='b', Prefix='nested/'))
            self.client.http = old
        self.assertEqual(len(pages), 2)
        self.assertEqual(requests[1]['cursor'], 'next')


    def test_backup_and_restore_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'source', Path(folder) / 'target'
            source.write_bytes(b'carousel-video')
            self.client.upload_file(str(source), 'b', 'clip/0', ExtraArgs={'ContentType': 'video/mp4'})
            self.client.download_file('b', 'clip/0', str(target))
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_public_and_management_credentials_are_refused(self):
        anon_payload = base64.urlsafe_b64encode(json.dumps({'role': 'anon'}).encode()).decode().rstrip('=')
        for key in ['sb_publishable_test', 'sbp_test', 'malformed', 'e30.'+anon_payload+'.fake']:
            with self.assertRaises(ValueError):
                SupabaseStorageClient('https://example.com', key)

    def test_legacy_service_jwt_uses_bearer_header(self):
        payload = base64.urlsafe_b64encode(json.dumps({'role': 'service_role'}).encode()).decode().rstrip('=')
        key = 'e30.'+payload+'.fake'
        client = SupabaseStorageClient('https://example.com', key)
        self.assertEqual(client.http.headers['authorization'], 'Bearer '+key)
        client.http.close()


class RevisionCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_rows_transfer_once_and_mutations_refresh(self):
        cache = RevisionCache()
        stamp, calls = 'one', 0
        async def revision(): return stamp
        async def fetch():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return [{'value': stamp}]
        results = await asyncio.gather(*(cache.read(('session', 0, 2000), revision, fetch) for _ in range(10)))
        self.assertEqual(calls, 1)
        results[0][0]['value'] = 'mutated'
        self.assertEqual((await cache.read(('session', 0, 2000), revision, fetch))[0]['value'], 'one')
        stamp = 'updated-or-deleted'
        await cache.read(('session', 0, 2000), revision, fetch)
        self.assertEqual(calls, 2)
        await cache.read(('other-session', 0, 2000), revision, fetch)
        self.assertEqual(calls, 3)

    async def test_size_and_entry_limits(self):
        cache = RevisionCache(max_bytes=80, max_entries=1)
        async def revision(): return 'one'
        async def fetch(): return ['small']
        await cache.read('a', revision, fetch)
        await cache.read('b', revision, fetch)
        self.assertEqual(list(cache.entries), ['b'])
        async def large(): return ['x' * 100]
        await cache.read('huge', revision, large)
        self.assertNotIn('huge', cache.entries)
        self.assertLessEqual(cache.bytes, 80)
