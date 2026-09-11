import * as React from "react"
import { Link, useLocation, useNavigate } from "react-router"
import { ArrowRight, Check, Eye, EyeOff, Fingerprint, Github, Loader2, Mail } from "lucide-react"
import { AuthShell } from "@/components/auth-shell"
import { BrandLogo } from "@/components/layout/brand-logo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/hooks/use-auth"
import { getSupabase } from "@/lib/supabase"
import { activeSession, authCapabilities, authError, passwordRules, safeDestination, validPassword, type AuthCapabilities } from "@/lib/auth-flow"

type Mode = "home" | "login" | "signup" | "forgot" | "reset" | "callback"
type Step = "email" | "password" | "signup" | "code" | "forgot" | "reset" | "mfa" | "done" | "callback"
type CodeKind = "email" | "signup" | "recovery"
const initialStep = (mode: Mode): Step => mode === "signup" ? "signup" : mode === "forgot" ? "forgot" : mode === "reset" ? "reset" : mode === "callback" ? "callback" : "email"

export function PasswordField({ value, onChange, confirm = false, rules = false }: { value: string; onChange: (value: string) => void; confirm?: boolean; rules?: boolean }) {
  const [visible, setVisible] = React.useState(false)
  const id = confirm ? "confirm-password" : "password"
  return <div className="auth-field"><label htmlFor={id}>{confirm ? "Confirm password" : rules ? "New password" : "Password"}</label><div className="auth-password"><Input id={id} type={visible ? "text" : "password"} autoComplete={rules || confirm ? "new-password" : "current-password"} required value={value} onChange={e => onChange(e.target.value)} /><button type="button" className="auth-eye" aria-label={`${visible ? "Hide" : "Show"} ${confirm ? "confirm password" : "password"}`} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={16}/> : <Eye size={16}/>}</button></div>{rules && <div className="auth-rules" aria-label="Password requirements">{passwordRules.map(rule => <span key={rule.label} data-valid={rule.test(value)}>{rule.test(value) ? "✓ " : ""}{rule.label}</span>)}</div>}</div>
}

export function LoginRoute({ mode = "login" }: { mode?: Mode }) {
  const auth = useAuth(), navigate = useNavigate(), location = useLocation()
  const [step, setStep] = React.useState<Step>(initialStep(mode))
  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [confirm, setConfirm] = React.useState("")
  const [name, setName] = React.useState("")
  const [code, setCode] = React.useState("")
  const [codeKind, setCodeKind] = React.useState<CodeKind>("email")
  const [factor, setFactor] = React.useState("")
  const [resetAfterMfa, setResetAfterMfa] = React.useState(false)
  const [error, setError] = React.useState("")
  const [busy, setBusy] = React.useState(false)
  const [oauthPending, setOauthPending] = React.useState(false)
  const [remaining, setRemaining] = React.useState(0)
  const [caps, setCaps] = React.useState<AuthCapabilities>({providers:[],passkeys:false})
  const [ready, setReady] = React.useState(false)
  const busyRef = React.useRef(false), callbackStarted = React.useRef(false)
  const popup = React.useRef<Window | null>(null)
  const popupTimer = React.useRef<number | undefined>(undefined)
  const query = new URLSearchParams(location.search)
  const destination = safeDestination(query.get("next") ?? (location.state as {from?:string} | null)?.from)
  const address = email.trim().toLowerCase()

  React.useEffect(() => { setStep(initialStep(mode)); setError(""); setPassword(""); setConfirm("") }, [mode])
  React.useEffect(() => { let alive = true; authCapabilities().then(value => { if(alive){setCaps(value);setReady(true)} }).catch(cause => {if(alive){setError(authError(cause));setReady(true)}}); return () => {alive=false} }, [])
  React.useEffect(() => { if(!remaining)return; const timer=window.setTimeout(()=>setRemaining(value=>Math.max(0,value-1)),1000);return()=>window.clearTimeout(timer) }, [remaining])
  React.useEffect(() => () => {window.clearInterval(popupTimer.current);popup.current?.close()}, [])

  async function perform(action: () => Promise<void>) {
    if(busyRef.current)return
    busyRef.current=true;setBusy(true);setError("")
    try {await action()} catch(cause){setError(authError(cause))} finally {busyRef.current=false;setBusy(false)}
  }
  async function requireSecondFactor(reset = false) {
    const {client,session} = await activeSession()
    const {data,error:failure}=await client.auth.mfa.getAuthenticatorAssuranceLevel(session.access_token)
    if(failure || !data)throw failure ?? new Error("Could not verify account security. Please try again.")
    if(data.nextLevel === "aal2" && data.currentLevel !== "aal2") {
      const {data:list,error:listError}=await client.auth.mfa.listFactors()
      if(listError)throw listError
      const selected=list.totp.find(item=>item.status === "verified")
      if(!selected)throw new Error("Use your enrolled second factor to continue. A supported authenticator is required.")
      setFactor(selected.id);setResetAfterMfa(reset);setCode("");setStep("mfa");return true
    }
    return false
  }
  async function finish() {
    if(await requireSecondFactor())return
    const {session}=await activeSession()
    await auth.completeSignIn(session.access_token)
    if(query.get("popup") === "1" && window.opener) {
      window.opener.postMessage({type:"carousel-oauth",ok:true},window.location.origin);window.close()
    } else navigate(destination,{replace:true})
  }
  async function sendCode(kind: CodeKind, again = false) {
    if(again && remaining)return
    const client=await getSupabase()
    const redirectTo=window.location.origin+(kind === "recovery" ? "/reset-password" : "/auth/confirm")
    const result=kind === "signup" ? await client.auth.resend({type:"signup",email:address,options:{emailRedirectTo:redirectTo}})
      : kind === "recovery" ? await client.auth.resetPasswordForEmail(address,{redirectTo})
      : await client.auth.signInWithOtp({email:address,options:{shouldCreateUser:false,emailRedirectTo:redirectTo}})
    if(result.error)throw result.error
    setCodeKind(kind);setCode("");setRemaining(60);setStep("code")
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if(step === "email"){setError("");setStep("password");return}
    await perform(async()=>{
      const client=await getSupabase()
      if(step === "password") {
        const {error:failure}=await client.auth.signInWithPassword({email:address,password})
        if(failure)throw failure
        await finish()
      } else if(step === "signup") {
        if(!validPassword(password))throw new Error("Complete the password requirements below.")
        if(password !== confirm)throw new Error("Your passwords don’t match yet.")
        const {data,error:failure}=await client.auth.signUp({email:address,password,options:{data:{username:name.trim()},emailRedirectTo:window.location.origin+"/auth/confirm"}})
        if(failure)throw failure
        setPassword("");setConfirm("")
        if(data.session)await finish()
        else {setCodeKind("signup");setStep("code");setRemaining(60)}
      } else if(step === "forgot") await sendCode("recovery")
      else if(step === "code") {
        const {error:failure}=await client.auth.verifyOtp({email:address,token:code.replace(/\s/g,""),type:codeKind})
        if(failure)throw failure
        if(codeKind === "recovery"){if(!await requireSecondFactor(true))setStep("reset")}
        else await finish()
      } else if(step === "mfa") {
        const {error:failure}=await client.auth.mfa.challengeAndVerify({factorId:factor,code})
        if(failure)throw failure
        if(resetAfterMfa)setStep("reset");else await finish()
      } else if(step === "reset") {
        if(!validPassword(password))throw new Error("Complete the password requirements below.")
        if(password !== confirm)throw new Error("Your passwords don’t match yet.")
        if(await requireSecondFactor(true))return
        const {error:failure}=await client.auth.updateUser({password})
        if(failure)throw failure
        await auth.signOut();setPassword("");setConfirm("");setStep("done");navigate("/login?reset=success",{replace:true})
      }
    })
  }

  React.useEffect(()=>{
    const params=new URLSearchParams(window.location.search),hash=new URLSearchParams(window.location.hash.slice(1))
    if(callbackStarted.current || !["callback","reset"].includes(mode))return
    callbackStarted.current=true
    void perform(async()=>{
      try {
      if(params.get("error") || hash.get("error"))throw new Error(params.get("error_description") ?? hash.get("error_description") ?? "This sign-in link is invalid or expired.")
      const client=await getSupabase()
      const type=params.get("type") ?? hash.get("type")
      if(params.has("token_hash")) {
        if(!["email","signup","recovery"].includes(type ?? ""))throw new Error("This sign-in link is not supported.")
        const {error:failure}=await client.auth.verifyOtp({token_hash:params.get("token_hash")!,type:type as CodeKind});if(failure)throw failure
      } else if(params.has("code")) {
        const {error:failure}=await client.auth.exchangeCodeForSession(params.get("code")!);if(failure)throw failure
      } else if(hash.has("access_token") && hash.has("refresh_token")) {
        const {error:failure}=await client.auth.setSession({access_token:hash.get("access_token")!,refresh_token:hash.get("refresh_token")!});if(failure)throw failure
      } else if(mode === "callback")throw new Error("This sign-in link is missing its confirmation. Please sign in again.")
      const clean=new URL(window.location.href);for(const field of ["code","token_hash","type"])clean.searchParams.delete(field);clean.hash="";window.history.replaceState(null,"",clean.pathname+clean.search)
      if(type === "recovery" || mode === "reset") {await activeSession();if(!await requireSecondFactor(true))setStep("reset")}
      else await finish()
      } catch(cause) {setStep("callback");throw cause}
    })
  },[mode])

  React.useEffect(()=>{
    function receive(event: MessageEvent) {
      if(event.origin!==window.location.origin || !popup.current || event.source!==popup.current || event.data?.type!=="carousel-oauth")return
      popup.current.close();popup.current=null;window.clearInterval(popupTimer.current);setOauthPending(false)
      if(event.data.ok!==true){setError("Sign-in did not finish. Please try again.");return}
      void perform(async()=>{await auth.refresh();navigate(destination,{replace:true})})
    }
    window.addEventListener("message",receive);return()=>window.removeEventListener("message",receive)
  },[auth.refresh,destination,navigate])
  async function oauth(provider: "google" | "github") {
    if(busyRef.current || popup.current)return
    window.clearInterval(popupTimer.current);setOauthPending(true)
    popup.current=window.open("about:blank","carousel-oauth","width=520,height=720,menubar=no,toolbar=no")
    await perform(async()=>{
      try {
      const client=await getSupabase()
      const redirectTo=window.location.origin+"/auth/callback?next="+encodeURIComponent(destination)+(popup.current?"&popup=1":"")
      const {data,error:failure}=await client.auth.signInWithOAuth({provider,options:{redirectTo,skipBrowserRedirect:true}})
      if(failure || !data.url){popup.current?.close();popup.current=null;throw failure ?? new Error("Could not start sign-in.")}
      if(!popup.current || popup.current.closed){window.location.assign(data.url);return}
      popup.current.location.replace(data.url)
      popupTimer.current=window.setInterval(()=>{if(popup.current?.closed){popup.current=null;window.clearInterval(popupTimer.current);setOauthPending(false);setError("Sign-in window closed. You can try again.")}},500)
      } catch(cause){popup.current?.close();popup.current=null;setOauthPending(false);throw cause}
    })
  }

  const signedIn=auth.status === "in" && ["home","login","signup"].includes(mode)
  const title=signedIn?"Welcome back.":step === "signup"?"Your next chapter starts here.":step === "email"?"Welcome to your studio.":step === "password"?"Good to see you again.":step === "forgot"?"Let’s get you back in.":step === "code"?"Check your inbox.":step === "mfa"?"One more step.":step === "reset"?"A fresh start.":step === "done"?"You’re all set.":"Opening your studio…"
  const subtitle=signedIn?"Your workspace is right where you left it.":step === "signup"?"A place for your ideas, and the agents to make them happen.":step === "email"?"Sign in to create something worth sharing.":step === "password"?"Enter your password, or choose another way below.":step === "forgot"?"We’ll send a secure code and link to your email.":step === "code"?"Enter the code from your email, or open its secure link.":step === "mfa"?"Enter the six-digit code from your authenticator app.":step === "reset"?"Choose a strong password you haven’t used here before.":step === "done"?"Your password has been updated. Sign in with your new password.":"Verifying your secure sign-in link."
  const showEmail=["email","signup","forgot"].includes(step)
  const emailChip=["password","code"].includes(step)
  const buttonLabel=step === "email"?"Continue with email":step === "signup"?"Create account":step === "forgot"?"Send reset code":step === "code"?"Verify email":step === "mfa"?"Verify and continue":step === "reset"?"Save new password":"Sign in"
  return <AuthShell><header className="auth-card-header"><BrandLogo className="auth-card-mark"/><span className="auth-eyebrow">YOUR CREATIVE WORKSPACE</span><h2>{title}</h2><p>{subtitle}</p></header>
    {auth.status === "pending" && !["callback","reset"].includes(mode)?<p className="auth-subtle" role="status">Checking your session…</p>:signedIn?<>{error&&<p role="alert" className="auth-alert">{error}</p>}<div className="auth-session"><span className="auth-session-icon"><Check size={17}/></span><div><p>{auth.identity?.email}</p><small>Signed in securely</small></div></div><Button asChild variant="brand" className="auth-submit"><Link to={destination}>Go to dashboard <ArrowRight size={16}/></Link></Button><p className="auth-switch"><button className="auth-text-button" onClick={()=>void perform(auth.signOut)} disabled={busy||oauthPending}>Use another account</button></p></>:step === "done"?<Button asChild variant="brand" className="auth-submit"><Link to="/login">Back to sign in <ArrowRight size={16}/></Link></Button>:<>
      {emailChip&&<div className="auth-email-chip"><span>{address}</span><button type="button" onClick={()=>{setError("");setCode("");setStep(codeKind === "signup" && step === "code"?"signup":"email")}} disabled={busy||oauthPending}>Change email</button></div>}
      {oauthPending&&<p role="status" className="auth-subtle">Complete sign-in in the window that opened. <button className="auth-text-button" onClick={()=>{popup.current?.close();popup.current=null;window.clearInterval(popupTimer.current);setOauthPending(false)}}>Cancel</button></p>}
      {query.get("reset") === "success" && <p role="status" className="auth-subtle">Password updated. Sign in with your new password.</p>}
      {error&&<p role="alert" className="auth-alert">{error}</p>}
      {step === "callback"?<div className="auth-options">{busy?<Loader2 className="animate-spin" size={20}/>:<Link className="auth-text-button" to="/login">Back to sign in</Link>}</div>:<form className="auth-form" onSubmit={submit}>
        <fieldset disabled={busy||oauthPending} className="auth-form">
          {step === "signup"&&<div className="auth-field"><label htmlFor="name">Your name <span className="auth-subtle">Optional</span></label><Input id="name" autoComplete="name" maxLength={80} value={name} onChange={e=>setName(e.target.value)} placeholder="How should we call you?"/></div>}
          {showEmail&&<div className="auth-field"><label htmlFor="email">Email address</label><Input id="email" type="email" autoComplete="email" placeholder="you@company.com" required value={email} onChange={e=>setEmail(e.target.value)}/></div>}
          {["password","signup","reset"].includes(step)&&<PasswordField value={password} onChange={setPassword} rules={step !== "password"}/>}
          {["signup","reset"].includes(step)&&<PasswordField value={confirm} onChange={setConfirm} confirm/>}
          {["code","mfa"].includes(step)&&<div className="auth-field auth-code"><label htmlFor="code">{step === "mfa"?"Authenticator code":"Email verification code"}</label><Input id="code" inputMode="numeric" autoComplete="one-time-code" pattern={step === "mfa"?"[0-9]{6}":"[0-9]{6,10}"} maxLength={step === "mfa"?6:10} required value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,""))} placeholder="······"/></div>}
          {step === "password"&&<div className="auth-subtle" style={{textAlign:"right"}}><button type="button" className="auth-text-button" onClick={()=>{setError("");setStep("forgot")}}>Forgot password?</button></div>}
          <Button type="submit" variant="brand" className="auth-submit" disabled={!ready || busy || oauthPending}>{busy?<><Loader2 size={16} className="animate-spin"/> Please wait…</>:<>{buttonLabel}<ArrowRight size={16}/></>}</Button>
        </fieldset>
      </form>}
      {step === "code"&&<div className="auth-options"><button type="button" disabled={busy||remaining>0} onClick={()=>void perform(()=>sendCode(codeKind,true))}>{remaining?`Resend in ${remaining}s`:"Resend code"}</button></div>}
      {step === "password"&&<div className="auth-options"><button disabled={busy||oauthPending} onClick={()=>void perform(()=>sendCode("email"))}><Mail size={14}/> Email me a code</button><button disabled={busy||oauthPending} onClick={()=>void perform(()=>sendCode("signup"))}>Confirm email</button></div>}
      {["email","signup","password"].includes(step)&&caps.providers.length>0&&<><div className="auth-divider">or continue with</div><div className="auth-socials">{caps.providers.map(provider=><button key={provider} className="auth-social" disabled={busy||oauthPending} onClick={()=>void oauth(provider)} aria-label={`Continue with ${provider === "google"?"Google":"GitHub"}`}>{provider === "github"?<Github size={17}/>:<svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M22 12.2c0-.7-.1-1.4-.2-2.2H12v4.3h5.6a4.8 4.8 0 0 1-2.1 3.1v2.8H19c2-1.9 3-4.6 3-8Z"/><path fill="#34A853" d="M12 22c2.7 0 5-.9 7-2.8l-3.5-2.8c-1 .7-2.1 1-3.5 1-2.7 0-5-1.8-5.8-4.2H2.6v2.9A10 10 0 0 0 12 22Z"/><path fill="#FBBC05" d="M6.2 13.2a6 6 0 0 1 0-3.4V6.9H2.6a10 10 0 0 0 0 9.2l3.6-2.9Z"/><path fill="#EA4335" d="M12 6.6c1.5 0 2.9.5 4 1.6l3-3A10 10 0 0 0 2.6 6.9l3.6 2.9A6.1 6.1 0 0 1 12 6.6Z"/></svg>}{provider === "google"?"Google":"GitHub"}</button>)}</div></>}
      {caps.passkeys&&["email","password"].includes(step)&&typeof window.PublicKeyCredential!=="undefined"&&<div className="auth-options"><button disabled={busy||oauthPending} onClick={()=>void perform(async()=>{const c=await getSupabase();const {error:failure}=await c.auth.signInWithPasskey();if(failure)throw failure;await finish()})}><Fingerprint size={16}/> Continue with a passkey</button></div>}
      <p className="auth-switch">{step === "signup"?<>Already have an account? <Link to="/login">Sign in</Link></>:step === "email" || step === "password"?<>New here? <Link to="/signup">Create your account</Link></>:<Link to="/login" onClick={()=>{setStep("email");setError("")}}>Back to sign in</Link>}</p>
    </>}
  </AuthShell>
}
