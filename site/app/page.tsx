import CTA from "@/components/CTA";
import Chevrons from "@/components/Chevrons";
import Features from "@/components/Features";
import Hero from "@/components/Hero";
import HowItWorks from "@/components/HowItWorks";
import Problem from "@/components/Problem";
import Proof from "@/components/Proof";
import SiteFooter from "@/components/SiteFooter";
import SiteHeader from "@/components/SiteHeader";
import TechnicianApp from "@/components/TechnicianApp";
import Testimonials from "@/components/Testimonials";

export default function Page() {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-ink focus:px-4 focus:py-2 focus:text-[14px] focus:text-paper"
      >
        Skip to content
      </a>

      {/* The wash runs behind the header as well as the hero so the colour
          starts at the very top of the page. This wrapper deliberately has no
          overflow-hidden — that would make the sticky header stick to the
          block instead of the viewport — so the chevron layer clips itself. */}
      <div className="relative">
        <div
          aria-hidden="true"
          className="wash absolute inset-x-0 top-0 -z-20 h-[1100px]"
        />
        <div
          aria-hidden="true"
          className="absolute inset-x-0 top-0 -z-10 h-[1100px] overflow-hidden"
        >
          <Chevrons className="absolute -right-24 top-16 h-[440px] w-[720px] opacity-70 max-lg:hidden" />
        </div>

        <SiteHeader />

        <main id="main">
          <Hero />
          <Problem />
          <HowItWorks />
          <Features />
          <TechnicianApp />
          <Proof />
          <Testimonials />
          <CTA />
        </main>
      </div>

      <SiteFooter />
    </>
  );
}
