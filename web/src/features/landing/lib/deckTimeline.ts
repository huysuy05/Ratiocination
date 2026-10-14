/**
 * The hero deck's 8-second loop (UI.md §16), as pure keyframe tracks sampled by time.
 *
 * Three demo cards: A (front), B (middle) and C (back). A is marked relevant (drags right, flies
 * out), B springs forward and is saved (drags up, flies up), C comes to the front and wobbles; then
 * the deck restacks: C eases back, B and then A rise from below the hero card's bottom edge, fully
 * opaque (no cross-fades: two half-transparent cards would ghost their text), into their slots. The
 * state at 8 s equals the state at 0 s.
 *
 * Rotations are CSS degrees (positive = clockwise). `y` values are deck pixels; while a card waits
 * for the restack it sits `rise` pixels lower (below the hero card's bottom edge, clipped by it), so
 * `rise` is measured from the layout and passed in.
 */

export const DECK_LOOP_SECONDS = 8;

export type Ease = (progress: number) => number;

export const linear: Ease = (p) => p;
export const easeIn: Ease = (p) => p * p * p;
export const easeOut: Ease = (p) => 1 - (1 - p) ** 3;
export const easeInOut: Ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2);

/**
 * A spring's step response (mass 1) squeezed into a segment of `duration` seconds: the "gentle"
 * spring of UI.md §3 by default, a touch less damped so it settles with a hint of overshoot.
 */
export function springEase(duration: number, stiffness = 260, damping = 26): Ease {
  const omega = Math.sqrt(stiffness);
  const zeta = damping / (2 * omega);
  const omegaD = omega * Math.sqrt(Math.max(1 - zeta * zeta, 1e-6));
  return (p) => {
    if (p >= 1) return 1;
    const t = p * duration;
    const decay = Math.exp(-zeta * omega * t);
    return 1 - decay * (Math.cos(omegaD * t) + ((zeta * omega) / omegaD) * Math.sin(omegaD * t));
  };
}

/** A keyframe: the value at time `t`; `ease` shapes the segment that ends here. */
export interface Key {
  t: number;
  v: number;
  ease?: Ease;
}

export type Track = readonly Key[];

/** Sample a track at `t` (held before the first and after the last key; equal times = a jump). */
export function sample(track: Track, t: number): number {
  const first = track[0];
  if (!first) return 0;
  if (t <= first.t) return first.v;
  for (let i = 1; i < track.length; i += 1) {
    const b = track[i]!;
    if (t <= b.t) {
      const a = track[i - 1]!;
      if (b.t === a.t || t === b.t) return b.v;
      const progress = (t - a.t) / (b.t - a.t);
      return a.v + (b.v - a.v) * (b.ease ?? linear)(progress);
    }
  }
  return track[track.length - 1]!.v;
}

export interface CardTracks {
  x: Track;
  y: Track;
  /** 1 while the card waits below the hero card for the restack, 0 in its slot. */
  rise: Track;
  scale: Track;
  rotate: Track;
  opacity: Track;
  /** Opacity of the card's stamp (RELEVANT on A, SAVE on B). */
  stamp: Track;
}

export interface CardFrame {
  x: number;
  y: number;
  scale: number;
  rotate: number;
  opacity: number;
  stamp: number;
}

/** Stack slots (UI.md §7 deck): front, middle, back. */
export const SLOTS = [
  { scale: 1, y: 0 },
  { scale: 0.96, y: 12 },
  { scale: 0.92, y: 24 },
] as const;

const hold = (v: number): Track => [{ t: 0, v }];

const springForward = springEase(0.5);
const springRise = springEase(0.55);

const A: CardTracks = {
  x: [
    { t: 1.0, v: 0 },
    { t: 1.7, v: 110, ease: easeInOut },
    { t: 2.1, v: 560, ease: easeIn },
    { t: 2.1, v: 0 },
  ],
  y: hold(0),
  rise: [
    { t: 2.1, v: 0 },
    { t: 2.1, v: 1 },
    { t: 7.35, v: 1 },
    { t: 7.9, v: 0, ease: springRise },
  ],
  scale: hold(1),
  rotate: [
    { t: 1.0, v: 0 },
    { t: 1.7, v: 7, ease: easeInOut },
    { t: 2.1, v: 16, ease: easeIn },
    { t: 2.1, v: 0 },
  ],
  opacity: [
    { t: 1.7, v: 1 },
    { t: 2.1, v: 0, ease: easeIn },
    { t: 2.1, v: 1 },
  ],
  stamp: [
    { t: 1.0, v: 0 },
    { t: 1.5, v: 1, ease: easeOut },
    { t: 2.1, v: 1 },
    { t: 2.1, v: 0 },
  ],
};

const B: CardTracks = {
  x: hold(0),
  y: [
    { t: 1.9, v: SLOTS[1].y },
    { t: 2.4, v: SLOTS[0].y, ease: springForward },
    { t: 3.9, v: SLOTS[0].y },
    { t: 4.5, v: -70, ease: easeInOut },
    { t: 4.9, v: -520, ease: easeIn },
    { t: 4.9, v: SLOTS[1].y },
  ],
  rise: [
    { t: 4.9, v: 0 },
    { t: 4.9, v: 1 },
    { t: 7.1, v: 1 },
    { t: 7.65, v: 0, ease: springRise },
  ],
  scale: [
    { t: 1.9, v: SLOTS[1].scale },
    { t: 2.4, v: SLOTS[0].scale, ease: springForward },
    { t: 4.9, v: SLOTS[0].scale },
    { t: 4.9, v: SLOTS[1].scale },
  ],
  rotate: hold(0),
  opacity: [
    { t: 4.5, v: 1 },
    { t: 4.9, v: 0, ease: easeIn },
    { t: 4.9, v: 1 },
  ],
  stamp: [
    { t: 3.9, v: 0 },
    { t: 4.35, v: 1, ease: easeOut },
    { t: 4.9, v: 1 },
    { t: 4.9, v: 0 },
  ],
};

const C: CardTracks = {
  x: [
    { t: 5.6, v: 0 },
    { t: 6.05, v: -26, ease: easeInOut },
    { t: 6.5, v: 0, ease: easeInOut },
  ],
  y: [
    { t: 2.0, v: SLOTS[2].y },
    { t: 2.5, v: SLOTS[1].y, ease: springForward },
    { t: 4.7, v: SLOTS[1].y },
    { t: 5.2, v: SLOTS[0].y, ease: springForward },
    { t: 6.95, v: SLOTS[0].y },
    { t: 7.5, v: SLOTS[2].y, ease: easeInOut },
  ],
  rise: hold(0),
  scale: [
    { t: 2.0, v: SLOTS[2].scale },
    { t: 2.5, v: SLOTS[1].scale, ease: springForward },
    { t: 4.7, v: SLOTS[1].scale },
    { t: 5.2, v: SLOTS[0].scale, ease: springForward },
    { t: 6.95, v: SLOTS[0].scale },
    { t: 7.5, v: SLOTS[2].scale, ease: easeInOut },
  ],
  rotate: [
    { t: 5.6, v: 0 },
    { t: 6.05, v: -3, ease: easeInOut },
    { t: 6.5, v: 0, ease: easeInOut },
  ],
  opacity: hold(1),
  stamp: hold(0),
};

export const DECK_TRACKS = { a: A, b: B, c: C } as const;
export type DeckCardId = keyof typeof DECK_TRACKS;

/** The state of every card at `seconds` (wrapped into the loop); `rise` in deck pixels. */
export function deckFrame(seconds: number, rise: number): Record<DeckCardId, CardFrame> {
  let t = seconds % DECK_LOOP_SECONDS;
  if (t < 0) t += DECK_LOOP_SECONDS;
  const frame = (tracks: CardTracks): CardFrame => ({
    x: sample(tracks.x, t),
    y: sample(tracks.y, t) + sample(tracks.rise, t) * rise,
    scale: sample(tracks.scale, t),
    rotate: sample(tracks.rotate, t),
    opacity: sample(tracks.opacity, t),
    stamp: sample(tracks.stamp, t),
  });
  return { a: frame(A), b: frame(B), c: frame(C) };
}
