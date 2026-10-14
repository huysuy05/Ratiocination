import { useEffect, useState } from "react";
import { useNavigation } from "react-router";

/** Thin accent bar at the top while a route (lazy page or loader) is loading longer than 150 ms. */
export function RouteProgress() {
  const navigation = useNavigation();
  const busy = navigation.state !== "idle";
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!busy) {
      setVisible(false);
      return;
    }
    const timer = window.setTimeout(() => setVisible(true), 150);
    return () => window.clearTimeout(timer);
  }, [busy]);

  if (!visible) return null;
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-x-0 top-0 z-[80] h-0.5 overflow-hidden">
      <div className="h-full origin-left animate-[rr-route-progress_2s_var(--motion-ease)_forwards] bg-[linear-gradient(90deg,var(--accent),var(--edge-cites))] shadow-[0_0_12px_var(--accent-glow)]" />
    </div>
  );
}
