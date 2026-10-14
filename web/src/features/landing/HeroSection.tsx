/**
 * Hero (UI.md §16): the logo lockup (the page's h1), then the hero card — a light "photograph"
 * surface in both themes — with the portrait loop flush right/bottom, the demo deck on the left and
 * the sticker bottom-left. Below 640 px the deck sits above the portrait.
 */
import { useEffect, useLayoutEffect, useRef } from "react";
import { useMotionPreference } from "../../lib/motion";
import { useTheme } from "../../lib/theme";
import { LANDING_ASSETS } from "./content";
import { HeroDeck } from "./HeroDeck";
import { useOnScreen, usePageVisible } from "./lib/hooks";
import { Sticker } from "./Sticker";

/** Deck design size: one 460 × 300 card plus the 24 px the back cards peek out. */
const DECK_WIDTH = 460;
/** Extra distance below the hero card's edge for a waiting card (clears its shadow). */
const RISE_MARGIN = 48;

function HeroPortrait({ reduced, playing }: { reduced: boolean; playing: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = true;
    if (playing) {
      const started = video.play() as Promise<void> | undefined;
      started?.catch(() => undefined);
    } else {
      video.pause();
    }
  }, [playing]);

  return (
    <div
      aria-hidden="true"
      className="relative aspect-[900/1046] w-[min(88%,360px)] self-end overflow-hidden sm:absolute sm:right-0 sm:bottom-0 sm:h-full sm:w-auto"
    >
      {reduced ? (
        <img className="lp-portrait" src={LANDING_ASSETS.poster} alt="" width={1080} height={1486} decoding="async" />
      ) : (
        <video
          ref={videoRef}
          className="lp-portrait"
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          poster={LANDING_ASSETS.poster}
          width={1080}
          height={1486}
          tabIndex={-1}
        >
          <source src={LANDING_ASSETS.videoWebm} type="video/webm" />
          <source src={LANDING_ASSETS.videoMp4} type="video/mp4" />
        </video>
      )}
    </div>
  );
}

export function HeroSection() {
  const { reduced } = useMotionPreference();
  const { resolved } = useTheme();
  const cardRef = useRef<HTMLDivElement>(null);
  const deckBoxRef = useRef<HTMLDivElement>(null);
  const riseRef = useRef(900);
  const onScreen = useOnScreen(cardRef);
  const pageVisible = usePageVisible();
  const playing = !reduced && onScreen && pageVisible;

  // A waiting card sits just below the hero card's bottom edge (in deck pixels).
  useLayoutEffect(() => {
    const card = cardRef.current;
    const box = deckBoxRef.current;
    if (!card || !box) return;
    const update = () => {
      const cardRect = card.getBoundingClientRect();
      const boxRect = box.getBoundingClientRect();
      const scale = boxRect.width > 0 ? boxRect.width / DECK_WIDTH : 1;
      if (cardRect.height > 0) riseRef.current = (cardRect.bottom - boxRect.top) / scale + RISE_MARGIN;
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(card);
    return () => observer.disconnect();
  }, []);

  return (
    <section aria-labelledby="landing-title" data-offscreen={onScreen ? undefined : "true"} className="mx-auto w-full max-w-[1440px] px-4 sm:px-6 lg:px-10">
      <h1 id="landing-title" className="flex justify-center pt-9 pb-8 sm:pt-12 sm:pb-10 xl:pt-14 xl:pb-[46px]">
        <img
          src={resolved === "dark" ? LANDING_ASSETS.logoWhite : LANDING_ASSETS.logo}
          alt="Ratiocination"
          width={LANDING_ASSETS.logoSize.width}
          height={LANDING_ASSETS.logoSize.height}
          className="h-auto w-full max-w-[964px]"
          fetchPriority="high"
        />
        <span className="sr-only">: a short daily deck of new arXiv papers, ranked for your research, with a reason for every card.</span>
      </h1>

      <div
        ref={cardRef}
        className={
          "relative isolate flex flex-col overflow-hidden rounded-[24px] bg-(--brand-tint) sm:block sm:h-[460px] sm:rounded-[30px] md:h-[520px] lg:h-[560px] xl:h-[640px] " +
          "[--deck-scale:0.64] min-[400px]:[--deck-scale:0.7] min-[480px]:[--deck-scale:0.84] sm:[--deck-scale:0.72] md:[--deck-scale:0.8] lg:[--deck-scale:0.9] xl:[--deck-scale:1]"
        }
      >
        <HeroPortrait reduced={reduced} playing={playing} />
        <div
          ref={deckBoxRef}
          aria-hidden="true"
          className={
            "relative z-10 order-first mx-auto mt-5 mb-4 h-[calc(324px*var(--deck-scale))] w-[calc(460px*var(--deck-scale))] " +
            "sm:absolute sm:m-0 sm:top-16 sm:left-6 md:top-[104px] md:left-8 lg:top-[108px] lg:left-14 xl:top-[158px] xl:left-[150px]"
          }
        >
          <div className="origin-top-left scale-(--deck-scale)">
            <HeroDeck playing={playing} reduced={reduced} riseRef={riseRef} />
          </div>
        </div>
        {/* Narrow screens: over the deck's lower-left corner (the portrait's empty top-left); wider: bottom-left. */}
        <Sticker className="absolute top-[calc(324px*var(--deck-scale)-8px)] left-3 z-20 sm:top-auto sm:bottom-6 sm:left-6 lg:bottom-7 lg:left-8 xl:bottom-10 xl:left-10" />
      </div>
    </section>
  );
}
