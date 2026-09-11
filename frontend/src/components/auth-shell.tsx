import type { ReactNode } from "react"
import { Link } from "react-router"
import { ArrowUpRight, Check, Layers3 } from "lucide-react"
import { BrandLogo } from "@/components/layout/brand-logo"
import { StudioArtwork } from "@/components/layout/studio-artwork"
import "@/routes/auth.css"

export function AuthShell({ children, step }: { children: ReactNode; step: string }) {
  return <main className="auth-page" data-auth-step={step}>
    <section className="auth-story" aria-label="Your creative studio">
      <Link className="auth-wordmark" to="/" aria-label="Carousel Factory home"><BrandLogo className="size-10"/><span>carousel<span className="auth-wordmark-dot">.</span></span></Link>
      <div className="auth-story-copy"><span className="auth-eyebrow"><span/> A little idea. A whole creative team.</span><h1>Make your next<br/>story <em>unmissable.</em></h1><p>Turn what’s happening into something<br className="auth-desktop-break"/> worth swiping. Your agents take it from here.</p></div>
      <div className="auth-art" aria-hidden="true">
        <div className="auth-art-orbit"/><div className="auth-art-orbit second"/>
        <div className="auth-mini-slide back"><span>02 / THE STORY</span><i/><i/><i/><div><Layers3 size={26}/><p>One idea.<br/>Every angle.</p></div></div>
        <div className="auth-mini-slide front"><div className="auth-mini-top"><span>THE NEXT CHAPTER</span><ArrowUpRight size={16}/></div><StudioArtwork name="carousel-sculpture" className="auth-sculpture" priority sizes="(max-width: 800px) 180px, 320px"/><div className="auth-mini-caption"><small>CREATED WITH CURIOSITY</small><strong>Good stories<br/>deserve great design.</strong><span>YOUR STUDIO <span>01 / 06</span></span></div></div>
        <div className="auth-art-note"><span><BrandLogo className="size-7"/></span><div>From brief to beautiful<small>Your creative team, in motion</small></div></div>
        <div className="auth-art-ready"><span><Check size={13}/></span> Ready for your final touch</div>
      </div>
      <div className="auth-story-footer"><span>Research</span><i/> <span>Create</span><i/><span>Make it yours</span><span className="auth-footer-index">01 — ∞</span></div>
    </section>
    <section className="auth-form-side" aria-label="Account access"><Link to="/" className="auth-mobile-brand"><BrandLogo className="size-9"/> Carousel Factory</Link><div className="auth-card">{children}</div><p className="auth-footnote">Your ideas. Your designs. Your own workspace.</p></section>
  </main>
}
