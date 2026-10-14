/**
 * Verify an email address from a link (UI.md §6). Posts the token on mount; success signs the user in
 * and continues to `next` (or `/`). Opened outside the browser that asked for the link, the server
 * first asks for the account's password (`password_required`), so a link alone never confirms a
 * password somebody else chose. An invalid link offers a new one.
 */
import { CircleX } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { isApiError } from "../api/client";
import { useMe, useResendVerification, useVerifyEmail } from "../api/hooks";
import { useAuthLayoutContext } from "../app/AuthLayout";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { Spinner } from "../components/ui/Spinner";
import { AuthHeading, FormAlert } from "../features/auth/AuthHeading";
import { formErrorText, safeNext } from "../features/auth/lib";
import { PasswordInput } from "../features/auth/PasswordInput";
import { ResendVerificationForm } from "../features/auth/ResendVerification";

const SIGNED_IN = "Email confirmed — you are signed in.";

/** The account email the server sends with `password_required` (shown read-only for password managers). */
function detailsEmail(error: unknown): string {
  if (!isApiError(error) || !error.details || !("email" in error.details)) return "";
  const email: unknown = error.details.email;
  return typeof email === "string" ? email : "";
}

export default function VerifyEmailPage() {
  const { config } = useAuthLayoutContext();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const next = safeNext(params.get("next"));
  const verify = useVerifyEmail();
  const resend = useResendVerification();
  const me = useMe({ retry: false });
  const navigate = useNavigate();
  const sent = useRef(false);
  // The account email once the server asked for the password ("" when it did not say), else null.
  const [passwordFor, setPasswordFor] = useState<string | null>(null);
  const [password, setPassword] = useState("");

  useEffect(() => {
    // Post the token once (StrictMode mounts effects twice in development).
    if (!token || sent.current) return;
    sent.current = true;
    verify.mutate(
      { token },
      {
        onSuccess: () => {
          toast.success(SIGNED_IN);
          void navigate(next, { replace: true });
        },
        onError: (error) => {
          if (error.code === "password_required") setPasswordFor(detailsEmail(error));
        },
      },
    );
  }, [token, verify, navigate, next]);

  if (verify.isSuccess) return <Navigate to={next} replace />;

  const errorCode = isApiError(verify.error) ? verify.error.code : null;
  const linkFailed = errorCode === "invalid_token" || errorCode === "not_allowed" || (verify.isError && passwordFor === null);

  if (passwordFor !== null && !linkFailed) {
    const wrongPassword = errorCode === "invalid_credentials";
    const formError = wrongPassword || errorCode === "password_required" ? null : formErrorText(verify.error);
    const onSubmit = (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (verify.isPending || !password) return;
      verify.mutate(
        { token, password },
        {
          onSuccess: () => {
            toast.success(SIGNED_IN);
            void navigate(next, { replace: true });
          },
        },
      );
    };
    return (
      <>
        <AuthHeading
          title="Confirm your email"
          subtitle="Enter your password to finish signing up. We ask because this link was opened outside the browser you signed up in."
        />
        {formError ? <FormAlert>{formError}</FormAlert> : null}
        <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
          {passwordFor ? (
            <Field label="Email">
              <Input type="email" name="email" autoComplete="username" readOnly value={passwordFor} />
            </Field>
          ) : null}
          <Field label="Password" error={wrongPassword ? "Wrong password." : undefined}>
            <PasswordInput
              name="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" size="lg" fullWidth loading={verify.isPending}>
            Confirm and sign in
          </Button>
        </form>
        <p className="mt-6 text-center text-[13.5px] text-muted">
          Forgot it, or you did not create this account?{" "}
          <Link to={paths.forgotPassword()} className="font-medium text-accent hover:underline">
            Reset the password
          </Link>
        </p>
      </>
    );
  }

  if (token && (verify.isPending || verify.isIdle)) {
    return (
      <div role="status" className="flex flex-col items-center gap-4 py-6 text-center">
        <AuthHeading title="Verify your email" />
        <Spinner className="size-6 text-accent" />
        <p className="text-muted">Checking your link…</p>
      </div>
    );
  }

  const failure =
    errorCode === "not_allowed"
      ? "This email address cannot use this server."
      : !token || errorCode === "invalid_token"
        ? "This verification link is invalid or has expired."
        : (formErrorText(verify.error) ?? "This verification link is invalid or has expired.");
  const signedInUnverified = me.data?.mode === "accounts" && !me.data.user.email_verified;
  const offerNewLink = !me.data && errorCode !== "not_allowed" && config.email_delivery && config.providers.password;

  return (
    <div className="flex flex-col items-center gap-4 text-center">
      <span className="grid size-14 place-items-center rounded-full bg-not-relevant/12 text-not-relevant ring-1 ring-not-relevant/25">
        <CircleX aria-hidden="true" className="size-7" />
      </span>
      <AuthHeading title="Verify your email" subtitle={failure} />
      {signedInUnverified ? (
        <>
          {resend.isSuccess ? <FormAlert tone="success">A new link is on its way to your inbox.</FormAlert> : null}
          {resend.isError ? <FormAlert>{formErrorText(resend.error)}</FormAlert> : null}
          <Button variant="primary" size="lg" fullWidth loading={resend.isPending} onClick={() => resend.mutate()}>
            Send a new link
          </Button>
        </>
      ) : offerNewLink ? (
        <>
          <p className="-mt-2 text-[13.5px] text-muted">Enter your email and we will send a new link.</p>
          <ResendVerificationForm next={next} />
        </>
      ) : null}
      {signedInUnverified ? null : (
        <Button asChild variant={offerNewLink ? "outline" : "primary"} size="lg" fullWidth>
          <Link to={me.data ? paths.settings() : paths.signIn(next)}>{me.data ? "Go to settings" : "Sign in"}</Link>
        </Button>
      )}
    </div>
  );
}
