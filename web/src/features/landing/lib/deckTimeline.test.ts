import { describe, expect, it } from "vitest";
import { DECK_LOOP_SECONDS, deckFrame, easeInOut, sample, SLOTS, springEase, type CardFrame } from "./deckTimeline";

const RISE = 700;

function close(a: CardFrame, b: CardFrame) {
  for (const key of Object.keys(a) as (keyof CardFrame)[]) expect(a[key]).toBeCloseTo(b[key], 1);
}

describe("sample", () => {
  const track = [
    { t: 1, v: 0 },
    { t: 2, v: 10 },
    { t: 2, v: -5 },
    { t: 3, v: 5, ease: easeInOut },
  ];

  it("holds before the first key and after the last", () => {
    expect(sample(track, 0)).toBe(0);
    expect(sample(track, 9)).toBe(5);
  });

  it("interpolates and jumps at repeated times", () => {
    expect(sample(track, 1.5)).toBeCloseTo(5);
    expect(sample(track, 2)).toBe(10);
    expect(sample(track, 2.0001)).toBeCloseTo(-5, 2);
    expect(sample(track, 2.5)).toBeCloseTo(0);
  });

  it("springs settle on their target", () => {
    const ease = springEase(0.5);
    expect(ease(0)).toBeCloseTo(0);
    expect(ease(0.98)).toBeCloseTo(1, 2);
    expect(ease(1)).toBe(1);
  });
});

describe("deckFrame", () => {
  it("starts as a stacked deck: A front, B middle, C back, no stamps", () => {
    const frame = deckFrame(0, RISE);
    expect(frame.a).toEqual({ x: 0, y: SLOTS[0].y, scale: SLOTS[0].scale, rotate: 0, opacity: 1, stamp: 0 });
    expect(frame.b).toEqual({ x: 0, y: SLOTS[1].y, scale: SLOTS[1].scale, rotate: 0, opacity: 1, stamp: 0 });
    expect(frame.c).toEqual({ x: 0, y: SLOTS[2].y, scale: SLOTS[2].scale, rotate: 0, opacity: 1, stamp: 0 });
  });

  it("ends where it starts, so the loop is seamless", () => {
    const start = deckFrame(0, RISE);
    const end = deckFrame(DECK_LOOP_SECONDS - 0.001, RISE);
    close(end.a, start.a);
    close(end.b, start.b);
    close(end.c, start.c);
    expect(deckFrame(DECK_LOOP_SECONDS + 1.7, RISE)).toEqual(deckFrame(1.7, RISE));
  });

  it("marks A relevant: drags right with its stamp, then flies out", () => {
    const dragged = deckFrame(1.7, RISE).a;
    expect(dragged.x).toBeCloseTo(110);
    expect(dragged.rotate).toBeCloseTo(7);
    expect(dragged.stamp).toBe(1);
    const gone = deckFrame(2.1, RISE).a;
    expect(gone.x).toBeCloseTo(560);
    expect(gone.opacity).toBe(0);
  });

  it("saves B: forward, drags up with its stamp, flies up", () => {
    expect(deckFrame(3, RISE).b).toMatchObject({ y: 0, scale: 1 });
    const dragged = deckFrame(4.5, RISE).b;
    expect(dragged.y).toBeCloseTo(-70);
    expect(dragged.stamp).toBe(1);
    expect(deckFrame(4.9, RISE).b.opacity).toBe(0);
  });

  it("brings C to the front, wobbles it left, and eases it back for the restack", () => {
    expect(deckFrame(5.4, RISE).c).toMatchObject({ y: 0, scale: 1 });
    const wobble = deckFrame(6.05, RISE).c;
    expect(wobble.x).toBeCloseTo(-26);
    expect(wobble.rotate).toBeCloseTo(-3);
    expect(deckFrame(7.5, RISE).c).toMatchObject({ y: SLOTS[2].y, scale: SLOTS[2].scale });
  });

  it("restacks B then A from below the hero card, fully opaque (no cross-fade)", () => {
    for (const t of [3, 5, 6, 7, 7.2, 7.4, 7.6]) {
      const { a, b } = deckFrame(t, RISE);
      if (t > 2.1) expect(a.opacity).toBe(1);
      if (t > 4.9) expect(b.opacity).toBe(1);
    }
    expect(deckFrame(6, RISE).a.y).toBe(RISE);
    expect(deckFrame(6, RISE).b.y).toBe(SLOTS[1].y + RISE);
    expect(deckFrame(7.65, RISE).b.y).toBeCloseTo(SLOTS[1].y, 0);
    expect(deckFrame(7.4, RISE).a.y).toBeGreaterThan(deckFrame(7.4, RISE).b.y);
    expect(deckFrame(7.9, RISE).a.y).toBeCloseTo(0, 0);
  });
});
