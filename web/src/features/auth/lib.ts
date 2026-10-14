/** Auth page helpers: safe `next` paths, OAuth error texts and API field errors (UI.md §6). */
import { getFieldErrors, getRetryAfterSeconds, isApiError } from "../../api/client";

/** An in-app path to continue to after signing in ("/" unless `raw` is a safe absolute path). */
export function safeNext(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  if (/^\/(signin|signup|forgot|reset|verify)(\/|\?|$)/.test(raw)) return "/";
  return raw;
}

/** `/signin?error=` codes set by the OAuth callback (API.md "Accounts"). */
export const OAUTH_ERRORS: Readonly<Record<string, string>> = {
  oauth_denied: "Sign-in was cancelled.",
  oauth_state: "That sign-in link expired. Please try again.",
  oauth_failed: "The provider could not complete the sign-in. Please try again.",
  no_verified_email: "Your provider account has no verified email address.",
  not_allowed: "This email address cannot use this server.",
  identity_in_use: "That account is already linked to another user.",
};

export function oauthErrorText(code: string | null): string | null {
  if (!code) return null;
  return OAUTH_ERRORS[code] ?? "Sign-in failed. Please try again.";
}

/** Field errors of a validation_error, keyed by the first path segment ("email", "password", …). */
export function fieldErrorMap(error: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of getFieldErrors(error)) {
    const key = field.path.split(".")[0] ?? field.path;
    if (!(key in out)) out[key] = field.message;
  }
  return out;
}

/** A friendly form-level message for an auth error (field errors are shown on their fields). */
export function formErrorText(error: unknown): string | null {
  if (!error) return null;
  if (!isApiError(error)) return "Something went wrong. Please try again.";
  switch (error.code) {
    case "invalid_credentials":
      return "Wrong email or password.";
    case "rate_limited": {
      const seconds = getRetryAfterSeconds(error);
      if (seconds === null) return "Too many attempts. Please wait a moment and try again.";
      const minutes = Math.ceil(seconds / 60);
      return `Too many attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    case "validation_error":
      return Object.keys(fieldErrorMap(error)).length > 0 ? null : error.message;
    case "network_error":
      return "Could not reach the server. Check your connection and try again.";
    default:
      return error.message;
  }
}

export const MIN_PASSWORD = 10;
