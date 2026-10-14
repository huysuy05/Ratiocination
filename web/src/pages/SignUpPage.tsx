/**
 * Create an account (UI.md §6): Google/GitHub, or name + email + password. When the server asks new
 * accounts to confirm their email, a successful sign-up shows "Check your inbox" instead of signing in;
 * when it cannot send that email, the password form is replaced by the providers and a short note.
 */
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { isApiError } from "../api/client";
import { isVerificationPending, useMe, useSignUp } from "../api/hooks";
import { useAuthLayoutContext } from "../app/AuthLayout";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { AuthHeading, FormAlert, OrDivider } from "../features/auth/AuthHeading";
import { fieldErrorMap, formErrorText, MIN_PASSWORD, safeNext } from "../features/auth/lib";
import { OAuthButtons } from "../features/auth/OAuthButtons";
import { PasswordInput } from "../features/auth/PasswordInput";
import { CheckInbox } from "../features/auth/ResendVerification";

export default function SignUpPage() {
  const { config } = useAuthLayoutContext();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const me = useMe({ retry: false });
  const signUp = useSignUp();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tooShort, setTooShort] = useState(false);

  if (signUp.data && isVerificationPending(signUp.data)) return <CheckInbox email={signUp.data.email} next={next} />;
  if (me.data) return <Navigate to={next} replace />;

  const fields = fieldErrorMap(signUp.error);
  const emailTaken = isApiError(signUp.error) && signUp.error.code === "email_taken";
  const formError = emailTaken ? null : formErrorText(signUp.error);
  const hasOAuth = config.providers.google || config.providers.github;
  const passwordSignUp = config.providers.password && config.password_signup;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (signUp.isPending) return;
    if (password.length < MIN_PASSWORD) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    const displayName = name.trim();
    signUp.mutate(
      { email: email.trim(), password, ...(displayName ? { display_name: displayName } : {}), ...(next !== "/" ? { next } : {}) },
      {
        onSuccess: (result) => {
          if (!isVerificationPending(result)) void navigate(next, { replace: true });
        },
      },
    );
  };

  return (
    <>
      <AuthHeading title="Create your account" subtitle="Find the papers that matter to your research, every day." />
      <OAuthButtons providers={config.providers} next={next} verb="Sign up" />
      {passwordSignUp ? (
        <>
          {hasOAuth ? <OrDivider /> : null}
          {formError ? <FormAlert>{formError}</FormAlert> : null}
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
            <Field label="Name" hint="Optional — shown only to you." error={fields.display_name}>
              <Input name="name" autoComplete="name" maxLength={100} autoFocus value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Field
              label="Email"
              error={
                emailTaken ? (
                  <>
                    An account with this email already exists.{" "}
                    <Link to={paths.signIn(next)} className="underline">
                      Sign in instead
                    </Link>
                  </>
                ) : (
                  fields.email
                )
              }
            >
              <Input
                type="email"
                name="email"
                autoComplete="email"
                inputMode="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <Field
              label="Password"
              error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : fields.password}
            >
              <PasswordInput
                name="password"
                autoComplete="new-password"
                required
                meter
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" size="lg" fullWidth loading={signUp.isPending}>
              Create account
            </Button>
            {config.email_verification ? (
              <p className="-mt-1 text-center text-[12.5px] text-faint">We will email you a link to confirm your address before you sign in.</p>
            ) : null}
          </form>
        </>
      ) : (
        <p className="mt-4 text-center text-[13.5px] text-muted">
          {!hasOAuth
            ? "Sign-up is closed on this server. Ask the administrator for an invitation."
            : config.providers.password
              ? "Signing up with an email and password is not available on this server. Use Google or GitHub above — they confirm your email for us."
              : "Use one of the providers above to create your account."}
        </p>
      )}
      <p className="mt-6 text-center text-[13.5px] text-muted">
        Already have an account?{" "}
        <Link to={paths.signIn(next)} className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </>
  );
}
