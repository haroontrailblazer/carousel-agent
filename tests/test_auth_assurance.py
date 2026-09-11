from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_api import auth
from web_api.auth import Identity, AuthError, enforce_assurance, issue_session_token, read_session_token

OWNER='11111111-1111-4111-8111-111111111111'
SECRET='test-only-secret-never-used-outside-local-tests-0123456789'

def test_only_verified_second_factor_claims_satisfy_enrolled_accounts():
    identity=Identity('user@example.test',OWNER,'admin',requires_mfa=True)
    for value in ('aal1','',None,'admin','AAL2'):
        with pytest.raises(AuthError,match='authenticator'):
            enforce_assurance(identity,value)
    assert enforce_assurance(identity,'aal2').assurance=='aal2'

def test_cookie_preserves_verified_assurance():
    identity=Identity('user@example.test',OWNER,'admin',assurance='aal2')
    signed=issue_session_token(identity,ttl_s=60,secret=SECRET)
    assert read_session_token(signed,secret=SECRET).assurance=='aal2'

def test_newly_enrolled_factor_blocks_old_cookie_and_bearer(monkeypatch):
    from app.services import instagram_accounts,telegram_config
    monkeypatch.setattr(instagram_accounts,'load',AsyncMock())
    monkeypatch.setattr(telegram_config,'load',AsyncMock())
    monkeypatch.setattr(auth,'authorize_email',AsyncMock(return_value=Identity('user@example.test',OWNER,'admin',requires_mfa=True)))
    api=FastAPI()
    @api.get('/api/private')
    async def private():return {'ok':True}
    verifier=Mock();verifier.verify.return_value={'email':'user@example.test','subject':OWNER,'claims':{'aal':'aal1'}}
    client=TestClient(auth.AuthMiddleware(api,verifier=verifier,secret=SECRET))
    old=issue_session_token(Identity('user@example.test',OWNER,'admin'),ttl_s=60,secret=SECRET)
    assert client.get('/api/private',headers={'Cookie':auth.COOKIE_NAME+'='+old}).status_code==401
    assert client.get('/api/private',headers={'Authorization':'Bearer normal-test-token'}).status_code==401
    upgraded=issue_session_token(Identity('user@example.test',OWNER,'admin',assurance='aal2'),ttl_s=60,secret=SECRET)
    assert client.get('/api/private',headers={'Cookie':auth.COOKIE_NAME+'='+upgraded}).status_code==200

@pytest.mark.asyncio
async def test_provisioned_workspace_reports_factor_requirement(monkeypatch):
    client=Mock();client.system_rpc=AsyncMock(return_value={'enabled':True,'requires_mfa':True})
    monkeypatch.setattr(auth.db,'get_pool',AsyncMock(return_value=client))
    assert (await auth.authorize_email('user@example.test',OWNER)).requires_mfa
