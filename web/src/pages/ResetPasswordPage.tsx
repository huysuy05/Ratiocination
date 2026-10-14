/** Choose a new password from a reset link (UI.md §6); success signs in and goes home. */
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { isApiError } from "../api/client";
import { useResetPassword } from "../api/hooks";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Field } from "../components/ui/Input";
import { AuthHeading, FormAlert } from "../features/auth/AuthHeading";
import { fieldErrorMap, formErrorText, MIN_PASSWORD } from "../features/auth/lib";
import { PasswordInput } from "../features/auth/PasswordInput";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const reset = useResetPassword();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [tooShort, setTooShort] = useState(false);

  const invalidToken = !token || (isApiError(reset.error) && reset.error.code === "invalid_token");
  if (invalidToken) {
    return (
      <>
        <AuthHeading title="Choose a new password" />
        <FormAlert>This reset link is invalid or has expired.</FormAlert>
        <Button asChild variant="primary" size="lg" fullWidth>
          <Link to={paths.forgotPassword()}>Request a new link</Link>
        </Button>
      </>
    );
  }

  const fields = fieldErrorMap(reset.error);
  const formError = formErrorText(reset.error);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (reset.isPending) return;
    if (password.length < MIN_PASSWORD) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    reset.mutate({ token, password }, { onSuccess: () => void navigate(paths.root(), { replace: true }) });
  };

  return (
    <>
      <AuthHeading title="Choose a new password" subtitle="You will be signed out everywhere else." />
      {formError ? <FormAlert>{formError}</FormAlert> : null}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <Field label="New password" error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : fields.password}>
          <PasswordInput
            name="password"
            autoComplete="new-password"
            autoFocus
            required
            meter
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Button type="submit" variant="primary" size="lg" fullWidth loading={reset.isPending}>
          Save and sign in
        </Button>
      </form>
    </>
  );
}
