// Local browser integration tests: all API, email and provider requests are mocked.
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');
const origin=process.env.AUTH_TEST_ORIGIN || 'http://127.0.0.1:4183', id='11111111-1111-4111-8111-111111111111';
(async()=>{fs.mkdirSync('.work/auth-redesign',{recursive:true});const browser=await chromium.launch({headless:true});let latestPage;try{
 for(const viewport of [{width:1365,height:1000},{width:390,height:844},{width:320,height:568}]){
 const ctx=await browser.newContext({viewport,reducedMotion:'reduce',colorScheme:viewport.width<500?'dark':'light'});
 let owner=false,failSignOut=false,mfa=false,savedPasskey=true,exchanges=0,signups=0,recoveries=0,updates=0,otpBody=null;
 const errors=[];const user=()=>({id,email:'alice@example.test',aud:'authenticated',role:'authenticated',email_confirmed_at:new Date().toISOString(),user_metadata:{username:'Alice'},app_metadata:{provider:'email'},factors:mfa?[{id:'totp1',factor_type:'totp',status:'verified',friendly_name:'Authenticator'}]:[]});
 const token=(aal='aal1')=>[Buffer.from(JSON.stringify({alg:'HS256',typ:'JWT'})).toString('base64url'),Buffer.from(JSON.stringify({sub:id,email:'alice@example.test',aud:'authenticated',role:'authenticated',aal,amr:[],exp:Math.floor(Date.now()/1000)+3600})).toString('base64url'),Buffer.from('local-test-signature-only').toString('base64url')].join('.');
 const session=(aal='aal1')=>({access_token:token(aal),refresh_token:'local-test-refresh',expires_in:3600,token_type:'bearer',user:user()});
 await ctx.route('**/*',async route=>{const req=route.request(),u=new URL(req.url()),body=()=>req.postDataJSON();const send=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 if(u.origin==='https://test.supabase.co'){
 if(u.pathname.endsWith('/settings'))return send({external:{google:true,github:true,email:true},passkeys_enabled:true});
 if(u.pathname.endsWith('/passkeys'))return send(savedPasskey?[{id:'key1',friendly_name:'Test laptop',created_at:new Date().toISOString()}]:[]);
 if(u.pathname.endsWith('/passkeys/key1')&&req.method()==='DELETE'){savedPasskey=false;return send({});}
 if(u.pathname.endsWith('/factors')&&req.method()==='POST')return send({id:'totp1',type:'totp',totp:{qr_code:'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="black"/></svg>',secret:'LOCALTESTONLY'}});
 if(u.pathname.endsWith('/factors/totp1')&&req.method()==='DELETE'){mfa=false;return send({});}
 if(u.pathname.endsWith('/signup')){signups++;return send({user:user(),session:null});}
 if(u.pathname.endsWith('/token'))return body().password==='wrong'?send({msg:'Invalid login credentials',code:'invalid_credentials'},400):send(session());
 if(u.pathname.endsWith('/recover')){recoveries++;return send({});}
 if(u.pathname.endsWith('/otp')){otpBody=body();return send({});}
 if(u.pathname.endsWith('/factors/totp1/challenge'))return send({id:'challenge1',expires_at:Math.floor(Date.now()/1000)+300});
 if(u.pathname.endsWith('/factors/totp1/verify')){if(body().code!=='123456')return send({msg:'Invalid OTP'},403);mfa=true;return send(session('aal2'));}
 if(u.pathname.endsWith('/verify'))return body().token==='123456'||body().token_hash==='valid-hash'?send(session()):send({msg:'Token has expired',code:'otp_expired'},403);
 if(u.pathname.endsWith('/user')){if(req.method()==='PUT')updates++;return send(user());}
 if(u.pathname.endsWith('/authorize'))return route.fulfill({contentType:'text/html',body:'<p>Local OAuth placeholder</p>'});
 return send({});
 }
 if(u.origin!==origin)return route.abort();
 if(!u.pathname.startsWith('/api/'))return route.continue();
 if(u.pathname==='/api/auth/config')return send({configured:true,supabase_url:'https://test.supabase.co',supabase_anon_key:'local-test-public'});
 if(u.pathname==='/api/auth/session'){if(req.method()==='DELETE'){if(failSignOut)return send({error:'Local simulated outage'},503);owner=false;return send({});}const claims=JSON.parse(Buffer.from(body().access_token.split('.')[1],'base64url'));if(mfa&&claims.aal!=='aal2')return send({error:'MFA required'},403);owner=true;exchanges++;return send({});}
 if(u.pathname==='/api/auth/me')return owner?send({...user(),source:'cookie',is_admin:false}):send({},401);
 if(!owner)return send({},401);
 if(u.pathname==='/api/meta')return send({agents:[],phases:[],statuses:[],accounts:[],reworkable_agents:[],max_slides:10,publish_configured:false});
 if(u.pathname==='/api/pulse')return send({running:0,awaiting_review:0,queued:0,failed:0,fetching:false});
 return send({items:[]});
 });
 const page=await ctx.newPage();latestPage=page;page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin+'/');await page.getByRole('button',{name:'Continue with email',exact:true}).waitFor();
 await page.screenshot({path:`.work/auth-redesign/login-${viewport.width}.png`,fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Landing overflow');
 await page.getByRole('link',{name:'Create your account',exact:true}).click();await page.getByRole('heading',{name:'Your next chapter starts here.',exact:true}).waitFor();
 await page.getByLabel('Email address',{exact:true}).fill('alice@example.test');await page.getByLabel('New password',{exact:true}).fill('short');await page.getByLabel('Confirm password',{exact:true}).fill('short');
 await page.getByRole('button',{name:'Create account',exact:true}).click();await page.getByRole('alert').filter({hasText:'requirements'}).waitFor();assert.equal(signups,0);
 await page.getByLabel('New password',{exact:true}).fill('Strong-password12!');await page.getByLabel('Confirm password',{exact:true}).fill('Other-password12!');await page.getByRole('button',{name:'Create account',exact:true}).click();await page.getByRole('alert').filter({hasText:'match'}).waitFor();assert.equal(signups,0);
 await page.getByLabel('Confirm password',{exact:true}).fill('Strong-password12!');await page.screenshot({path:`.work/auth-redesign/signup-${viewport.width}.png`,fullPage:true});
 await page.getByRole('button',{name:'Create account',exact:true}).click();await page.getByLabel('Email verification code').fill('000000');await page.getByRole('button',{name:'Verify email',exact:true}).click();await page.getByRole('alert').filter({hasText:'expired'}).waitFor();assert.equal(exchanges,0);assert.equal(signups,1);assert(await page.getByRole('button',{name:/Resend in/}).isDisabled());
 await page.getByLabel('Email verification code').fill('123456');await page.getByRole('button',{name:'Verify email',exact:true}).click();await page.waitForURL('**/new');assert.equal(exchanges,1);
 await page.goto(origin+'/');await page.getByRole('link',{name:'Go to dashboard'}).waitFor();assert.equal(await page.getByLabel('Email address').count(),0);
 failSignOut=true;await page.getByRole('button',{name:'Use another account'}).click();await page.getByRole('alert').filter({hasText:'Could not sign out'}).waitFor();assert.equal(owner,true);await page.getByRole('link',{name:'Go to dashboard'}).waitFor();failSignOut=false;
 await page.getByRole('button',{name:'Use another account'}).click();await page.getByLabel('Email address').waitFor();assert.equal(owner,false);
 await page.getByLabel('Email address').fill('alice@example.test');await page.getByRole('button',{name:'Continue with email',exact:true}).click();await page.getByLabel('Password',{exact:true}).fill('wrong');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByRole('alert').filter({hasText:'don’t match'}).waitFor();
 await page.getByRole('button',{name:'Email me a code'}).click();await page.getByLabel('Email verification code').waitFor();assert.equal(otpBody.create_user,false);
 await page.goto(origin+'/forgot-password');await page.getByLabel('Email address').fill('alice@example.test');await page.getByRole('button',{name:'Send reset code'}).click();await page.getByLabel('Email verification code').waitFor();assert.equal(recoveries,1);await page.getByLabel('Email verification code').fill('123456');await page.getByRole('button',{name:'Verify email',exact:true}).click();await page.getByLabel('New password').fill('Changed-password12!');await page.getByLabel('Confirm password',{exact:true}).fill('Changed-password12!');await page.getByRole('button',{name:'Save new password'}).click();await page.waitForURL('**/login?reset=success');await page.getByRole('status').filter({hasText:'Password updated'}).waitFor();assert.equal(updates,1);assert.equal(owner,false);
 await page.goto(origin+'/reset-password');await page.getByRole('alert').waitFor();assert.equal(await page.getByLabel('New password').count(),0,'Expired reset must not expose active form');
 mfa=true;await page.goto(origin+'/login?next=%2F%2Fevil.example');await page.getByLabel('Email address').fill('alice@example.test');await page.getByRole('button',{name:'Continue with email',exact:true}).click();await page.getByLabel('Password',{exact:true}).fill('Strong-password12!');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.getByLabel('Authenticator code').waitFor();assert.equal(exchanges,1,'MFA must precede app session');await page.getByLabel('Authenticator code').fill('123456');await page.getByRole('button',{name:'Verify and continue'}).click();await page.waitForURL('**/new');assert.equal(exchanges,2);
 await page.goto(origin+'/');await page.getByRole('button',{name:'Use another account'}).click();await page.getByLabel('Email address').waitFor();mfa=false;
 await page.goto(origin+'/auth/confirm?token_hash=valid-hash&type=signup');await page.waitForURL('**/new');assert.equal(exchanges,3,'Callback exchange only once');
 await page.goto(origin+'/');await page.getByRole('button',{name:'Use another account'}).click();await page.getByLabel('Email address').waitFor();
 const popupPromise=page.waitForEvent('popup');await page.getByRole('button',{name:'Continue with GitHub'}).click();const popup=await popupPromise;await page.getByRole('status').filter({hasText:'Complete sign-in'}).waitFor();assert(await page.getByRole('button',{name:'Continue with Google'}).isDisabled());await popup.close();await page.getByRole('alert').filter({hasText:'window closed'}).waitFor();assert.equal(await page.getByRole('button',{name:'Continue with GitHub'}).isDisabled(),false);
 await page.getByLabel('Email address').fill('alice@example.test');await page.getByRole('button',{name:'Continue with email',exact:true}).click();await page.getByLabel('Password',{exact:true}).fill('Strong-password12!');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForURL('**/new');
 await page.goto(origin+'/profile');await page.getByRole('button',{name:'Set up authenticator'}).click();await page.getByLabel('Authenticator code').fill('123456');await page.getByRole('button',{name:'Verify and enable'}).click();await page.getByRole('status').filter({hasText:'Two-factor authentication is on'}).waitFor();assert.equal(mfa,true);
 await page.screenshot({path:`.work/auth-redesign/security-${viewport.width}.png`,fullPage:true});
 await page.getByRole('button',{name:'Remove authenticator',exact:true}).click();await page.getByLabel('Authenticator code').fill('000000');await page.getByRole('button',{name:'Verify and remove',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(mfa,true);
 await page.getByLabel('Authenticator code').fill('123456');await page.getByRole('button',{name:'Verify and remove',exact:true}).click();await page.getByRole('status').filter({hasText:'Authenticator removed'}).waitFor();assert.equal(mfa,false);
 await page.getByText('Test laptop',{exact:true}).waitFor();await page.getByRole('button',{name:'Remove',exact:true}).click();assert.equal(savedPasskey,true);await page.getByRole('button',{name:'Remove passkey',exact:true}).click();await page.getByRole('status').filter({hasText:'Passkey removed'}).waitFor();assert.equal(savedPasskey,false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);console.log(`PASS ${viewport.width}: signup/rules/OTP, cookie welcome, account switch, password errors, recovery, MFA, callback, safe redirect, popup cancellation, MFA enrollment/removal, passkey list/removal, no overflow/errors`);await ctx.close();
 }
 }catch(error){if(latestPage&&!latestPage.isClosed()){await latestPage.screenshot({path:'.work/auth-redesign/failure.png',fullPage:true});console.log(await latestPage.locator('body').innerText());}throw error}finally{await browser.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});


