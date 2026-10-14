/**
 * Sign in (UI.md §6): Google/GitHub, then email + password. A right password for an account whose
 * email is not confirmed yet (`email_unverified`) offers to resend the verification email.
 */
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { isApiError } from "../api/client";
import { useMe, useSignIn } from "../api/hooks";
import { useAuthLayoutContext } from "../app/AuthLayout";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { AuthHeading, FormAlert, OrDivider } from "../features/auth/AuthHeading";
import { fieldErrorMap, formErrorText, oauthErrorText, safeNext } from "../features/auth/lib";
import { OAuthButtons } from "../features/auth/OAuthButtons";
import { PasswordInput } from "../features/auth/PasswordInput";
import { ResendVerificationButton } from "../features/auth/ResendVerification";

export default function SignInPage() {
  const { config } = useAuthLayoutContext();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const oauthError = oauthErrorText(params.get("error"));
  const me = useMe({ retry: false });
  const signIn = useSignIn();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  if (me.data) return <Navigate to={next} replace />;

  const fields = fieldErrorMap(signIn.error);
  const unverifiedEmail = isApiError(signIn.error) && signIn.error.code === "email_unverified" ? (signIn.variables?.email ?? email.trim()) : null;
  const formError = unverifiedEmail ? null : formErrorText(signIn.error);
  const hasOAuth = config.providers.google || config.providers.github;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (signIn.isPending) return;
    signIn.mutate({ email: email.trim(), password }, { onSuccess: () => void navigate(next, { replace: true }) });
  };

  return (
    <>
      <AuthHeading title="Sign in" subtitle="Welcome back to Ratiocination." />
      {oauthError ? <FormAlert>{oauthError}</FormAlert> : null}
      <OAuthButtons providers={config.providers} next={next} />
      {config.providers.password ? (
        <>
          {hasOAuth ? <OrDivider /> : null}
          {formError ? <FormAlert>{formError}</FormAlert> : null}
          {unverifiedEmail ? (
            <div className="mb-4 flex flex-col gap-3 rounded-control border border-border-strong bg-surface-2 px-3.5 py-3 text-[13.5px] text-muted" role="status">
              <p>
                <span className="font-medium text-text">Confirm your email first.</span> Open the link we sent to{" "}
                <span className="font-medium break-all text-text">{unverifiedEmail}</span> when you signed up — it signs you in.
              </p>
              <ResendVerificationButton key={unverifiedEmail} email={unverifiedEmail} next={next} variant="secondary" />
            </div>
          ) : null}
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
            <Field label="Email" error={fields.email}>
              <Input
                type="email"
                name="email"
                autoComplete="email"
                inputMode="email"
                autoFocus
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <Field label="Password" error={fields.password}>
              <PasswordInput
                name="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
            <div className="-mt-1 flex justify-end">
              <Link to={paths.forgotPassword()} className="text-[13px] font-medium text-accent hover:underline">
                Forgot password?
              </Link>
            </div>
            <Button type="submit" variant="primary" size="lg" fullWidth loading={signIn.isPending}>
              Sign in
            </Button>
          </form>
          <p className="mt-6 text-center text-[13.5px] text-muted">
            New here?{" "}
            <Link to={paths.signUp(next)} className="font-medium text-accent hover:underline">
              Create an account
            </Link>
          </p>
        </>
      ) : null}
      {config.allowlist ? (
        <p className="mt-4 text-center text-[12.5px] text-faint">This server only admits invited email addresses.</p>
      ) : null}
    </>
  );
}
