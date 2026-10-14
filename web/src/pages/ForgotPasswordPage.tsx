/** Ask for a password reset link (UI.md §6). The answer never reveals whether the address exists. */
import { MailCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useForgotPassword } from "../api/hooks";
import { useAuthLayoutContext } from "../app/AuthLayout";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";
import { AuthHeading, FormAlert } from "../features/auth/AuthHeading";
import { fieldErrorMap, formErrorText } from "../features/auth/lib";

export default function ForgotPasswordPage() {
  const { config } = useAuthLayoutContext();
  const forgot = useForgotPassword();
  const [email, setEmail] = useState("");

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (forgot.isPending) return;
    forgot.mutate({ email: email.trim() });
  };

  if (forgot.isSuccess) {
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <span className="grid size-14 place-items-center rounded-full bg-accent/12 text-accent ring-1 ring-accent/25">
          <MailCheck aria-hidden="true" className="size-6" />
        </span>
        <AuthHeading
          title="Check your inbox"
          subtitle="If an account exists for that address, a link to reset your password is on its way. It works for 30 minutes."
        />
        <Link to={paths.signIn()} className="font-medium text-accent hover:underline">
          Back to sign in
        </Link>
      </div>
    );
  }

  const fields = fieldErrorMap(forgot.error);
  const formError = formErrorText(forgot.error);
  return (
    <>
      <AuthHeading title="Reset your password" subtitle="Enter your email and we will send you a reset link." />
      {!config.email_delivery ? (
        <FormAlert tone="info">Email is not set up on this server. Ask the administrator for a reset link.</FormAlert>
      ) : null}
      {formError ? <FormAlert>{formError}</FormAlert> : null}
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
        <Button type="submit" variant="primary" size="lg" fullWidth loading={forgot.isPending} disabled={!config.email_delivery}>
          Send reset link
        </Button>
      </form>
      <p className="mt-6 text-center text-[13.5px] text-muted">
        Remembered it?{" "}
        <Link to={paths.signIn()} className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </>
  );
}
