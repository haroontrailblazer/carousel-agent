"""Native Supabase Storage transport for the existing artifact and avatar stores.

The small boto-compatible surface preserves object paths and ADK metadata from
the previous S3 transport. No S3 credentials or public buckets are needed.
All methods run in the artifact service's worker threads.
"""
from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import quote

import httpx
from botocore.exceptions import ClientError


class SupabaseStorageClient:
    def __init__(self, url: str, key: str, *, connect_timeout=10.0,
                 read_timeout=120.0, transport=None):
        if not url or not key:
            raise ValueError("Native Storage requires SUPABASE_URL and a server-only Storage key")
        if not key.startswith('sb_secret_'):
            try:
                payload = key.split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                valid = isinstance(claims, dict) and claims.get('role') == 'service_role'
            except (IndexError, ValueError, TypeError):
                valid = False
            if not valid:
                raise ValueError('Storage requires a server secret or service-role key, not an anon key or management token')
        self.url = url.rstrip('/')
        headers = {"apikey": key}
        # New sb_secret keys authenticate at the gateway; legacy service-role
        # JWTs also need Authorization. Never pass a publishable/anon key here.
        if not key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + key
        self.http = httpx.Client(
            base_url=self.url + '/storage/v1/', headers=headers,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport, follow_redirects=False,
        )

    @staticmethod
    def _path(bucket, key=''):
        return quote(bucket, safe='') + ('/' + quote(key, safe='/') if key else '')

    def _request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        if not response.is_success:
            try:
                error = response.json()
            except ValueError:
                error = {}
            status = int(error.get('statusCode') or response.status_code)
            code = error.get('code') or error.get('error') or str(status)
            if status == 404 or code in ('NoSuchKey', 'not_found'):
                code = 'NoSuchKey'
            # Do not include signed URLs, response bodies or request headers
            # in exceptions: callers log storage failures.
            raise ClientError({'Error': {'Code': str(code), 'Message': 'Storage request failed'},
                               'ResponseMetadata': {'HTTPStatusCode': status}}, method)
        return response

    def put_object(self, *, Bucket, Key, Body, ContentType='application/octet-stream',
                   Metadata=None, CacheControl='private, max-age=3600'):
        headers = {'Content-Type': ContentType, 'Cache-Control': CacheControl,
                   'x-upsert': 'true'}
        if Metadata:
            headers['x-metadata'] = base64.b64encode(
                json.dumps(Metadata, ensure_ascii=True).encode()).decode()
        self._request('POST', 'object/' + self._path(Bucket, Key),
                      content=Body, headers=headers)

    def head_object(self, *, Bucket, Key):
        info = self._request('GET', 'object/info/authenticated/' +
                             self._path(Bucket, Key)).json()
        return {'Metadata': info.get('metadata') or {},
                'ContentType': info.get('content_type'),
                'LastModified': datetime.fromisoformat(info['last_modified'].replace('Z', '+00:00'))}

    def get_object(self, *, Bucket, Key):
        # Download responses do not carry custom metadata; info is small and
        # keeps old text/file-reference/display-name artifacts round-trippable.
        info = self.head_object(Bucket=Bucket, Key=Key)
        response = self._request('GET', 'object/authenticated/' + self._path(Bucket, Key))
        return {**info, 'Body': BytesIO(response.content)}

    def get_paginator(self, operation):
        if operation != 'list_objects_v2':
            raise ValueError('Unsupported Storage listing')
        return self

    def paginate(self, *, Bucket, Prefix=''):
        cursor = None
        while True:
            body = {'prefix': Prefix, 'limit': 1000, 'with_delimiter': False,
                    'sortBy': {'column': 'name', 'order': 'asc'}}
            if cursor:
                body['cursor'] = cursor
            page = self._request('POST', 'object/list-v2/' + self._path(Bucket), json=body).json()
            yield {'Contents': [
                {'Key': obj['name'], 'Size': (obj.get('metadata') or {}).get('size', 0),
                 'ETag': (obj.get('metadata') or {}).get('eTag', ''),
                 'LastModified': datetime.fromisoformat(
                    (obj.get('updated_at') or obj['created_at']).replace('Z', '+00:00'))}
                for obj in page.get('objects', [])
            ]}
            if not page.get('hasNext'):
                return
            next_cursor = page.get('nextCursor')
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError('Storage returned an invalid pagination cursor')
            cursor = next_cursor

    def delete_objects(self, *, Bucket, Delete):
        self._request('DELETE', 'object/' + self._path(Bucket),
                      json={'prefixes': [item['Key'] for item in Delete['Objects']]})

    def download_file(self, bucket, key, filename):
        # Backup utility compatibility. Stream covers to disk in bounded chunks.
        with self.http.stream('GET', 'object/authenticated/' + self._path(bucket, key)) as response:
            if not response.is_success:
                raise RuntimeError(f'Storage download failed ({response.status_code})')
            with Path(filename).open('wb') as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.put_object(Bucket=bucket, Key=key, Body=Path(filename).read_bytes(), **(ExtraArgs or {}))

    def delete_object(self, *, Bucket, Key):
        self.delete_objects(Bucket=Bucket, Delete={'Objects': [{'Key': Key}]})

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        if operation != 'get_object':
            raise ValueError('Only download URLs may be signed')
        data = self._request('POST', 'object/sign/' + self._path(Params['Bucket'], Params['Key']),
                             json={'expiresIn': ExpiresIn}).json()
        path = data['signedURL']
        if not path.startswith('/object/sign/'):
            raise RuntimeError('Storage returned an unexpected signed path')
        return self.url + '/storage/v1' + path
