/**
 * Email verification helpers for the auth pages (UI.md §6): the "Check your inbox" state after a
 * sign-up that must confirm its email, a "Resend email" button with a cooldown, and a small form that
 * asks for a new link by address. Every resend answers the same way whether or not the address has
 * an account (API.md: POST /api/auth/email/resend).
 */
import { MailCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useResendVerificationEmail } from "../../api/hooks";
import { paths } from "../../app/paths";
import { Button, type ButtonVariant } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Input";
import { AuthHeading, FormAlert } from "./AuthHeading";
import { fieldErrorMap, formErrorText } from "./lib";

/** Seconds before another verification email may be requested. */
export const RESEND_COOLDOWN_SECONDS = 60;

/** A countdown in whole seconds (0 = ready); `restart(n)` starts it again. */
function useCooldown(initialSeconds: number): [number, (seconds: number) => void] {
  const [remaining, setRemaining] = useState(initialSeconds);
  useEffect(() => {
    if (remaining <= 0) return;
    const id = window.setTimeout(() => setRemaining((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(id);
  }, [remaining]);
  return [remaining, setRemaining];
}

function resendBody(email: string, next: string) {
  return next !== "/" ? { email, next } : { email };
}

/** "Resend email" for `email`, disabled while the cooldown runs (`coolDownFirst`: an email was just sent). */
export function ResendVerificationButton({
  email,
  next = "/",
  coolDownFirst = false,
  variant = "outline",
}: {
  email: string;
  next?: string;
  coolDownFirst?: boolean;
  variant?: ButtonVariant;
}) {
  const resend = useResendVerificationEmail();
  const [cooldown, restart] = useCooldown(coolDownFirst ? RESEND_COOLDOWN_SECONDS : 0);
  const error = formErrorText(resend.error);
  const onClick = () => {
    if (cooldown > 0 || resend.isPending) return;
    resend.mutate(resendBody(email, next), { onSuccess: () => restart(RESEND_COOLDOWN_SECONDS) });
  };
  return (
    <div className="flex w-full flex-col gap-2">
      {resend.isSuccess ? <FormAlert tone="success">A new link is on its way to {email}.</FormAlert> : null}
      {error ? <FormAlert>{error}</FormAlert> : null}
      <Button variant={variant} size="lg" fullWidth loading={resend.isPending} disabled={cooldown > 0} onClick={onClick}>
        Resend email
      </Button>
      {cooldown > 0 ? (
        <p className="text-center text-[12.5px] text-faint tabular-nums">You can ask for another email in {cooldown} s.</p>
      ) : null}
    </div>
  );
}

/** Shown after a sign-up that must confirm its email: no session yet, the link signs in. */
export function CheckInbox({ email, next = "/" }: { email: string; next?: string }) {
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <span className="grid size-14 place-items-center rounded-full bg-accent/12 text-accent ring-1 ring-accent/25">
        <MailCheck aria-hidden="true" className="size-6" />
      </span>
      <AuthHeading
        title="Check your inbox"
        subtitle={
          <>
            We sent a link to <strong className="font-medium break-all text-text">{email}</strong>. Open it to finish signing up — it works for 48
            hours.
          </>
        }
      />
      <p className="-mt-3 text-[13px] text-muted">No email? Check your spam folder, or send it again.</p>
      <ResendVerificationButton email={email} next={next} coolDownFirst />
      <Link to={paths.signIn(next)} className="font-medium text-accent hover:underline">
        Back to sign in
      </Link>
    </div>
  );
}

/** Ask for a new verification link by address (signed out); the answer never reveals an account. */
export function ResendVerificationForm({ next = "/" }: { next?: string }) {
  const resend = useResendVerificationEmail();
  const [email, setEmail] = useState("");
  const fields = fieldErrorMap(resend.error);
  const error = formErrorText(resend.error);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (resend.isPending || !email.trim()) return;
    resend.mutate(resendBody(email.trim(), next));
  };
  if (resend.isSuccess) {
    return <FormAlert tone="success">If an account is waiting for that address, a new link is on its way.</FormAlert>;
  }
  return (
    <form onSubmit={onSubmit} noValidate className="flex w-full flex-col gap-3 text-left">
      {error ? <FormAlert>{error}</FormAlert> : null}
      <Field label="Email" error={fields.email}>
        <Input type="email" name="email" autoComplete="email" inputMode="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
      </Field>
      <Button type="submit" variant="primary" size="lg" fullWidth loading={resend.isPending}>
        Send a new link
      </Button>
    </form>
  );
}
