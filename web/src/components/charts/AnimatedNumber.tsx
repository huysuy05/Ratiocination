/** A number that counts up to its value once (jumps under reduced motion). Screen readers get the final value. */
import { animate } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { useMotionPreference } from "../../lib/motion";

export interface AnimatedNumberProps {
  value: number;
  format?: (value: number) => string;
  className?: string;
}

const defaultFormat = (value: number) => new Intl.NumberFormat("en").format(Math.round(value));

export function AnimatedNumber({ value, format = defaultFormat, className }: AnimatedNumberProps) {
  const { reduced } = useMotionPreference();
  const from = useRef(0);
  const [shown, setShown] = useState(reduced ? value : 0);

  useEffect(() => {
    if (reduced) {
      from.current = value;
      setShown(value);
      return;
    }
    const controls = animate(from.current, value, {
      duration: 0.9,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (latest) => setShown(latest),
    });
    from.current = value;
    return () => controls.stop();
  }, [value, reduced]);

  return (
    <span className={className}>
      <span aria-hidden="true" className="tabular-nums">
        {format(shown)}
      </span>
      <span className="sr-only">{format(value)}</span>
    </span>
  );
}
