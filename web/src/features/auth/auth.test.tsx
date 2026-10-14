import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AuthLayout } from "../../app/AuthLayout";
import ForgotPasswordPage from "../../pages/ForgotPasswordPage";
import ResetPasswordPage from "../../pages/ResetPasswordPage";
import SignInPage from "../../pages/SignInPage";
import SignUpPage from "../../pages/SignUpPage";
import VerifyEmailPage from "../../pages/VerifyEmailPage";
import { makeAuthConfig, makeMe } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type TestHandler } from "../../test/utils";
import { safeNext } from "./lib";

function server(extra: TestHandler, config = makeAuthConfig()): TestHandler {
  return async (request) => {
    if (request.path === "/api/auth/config") return jsonResponse(config);
    if (request.path === "/api/me" && request.method === "GET") return errorResponse(401, "unauthenticated", "Sign in to continue");
    return extra(request);
  };
}

function Home() {
  return <h1>Home</h1>;
}

function renderAuth(route: string) {
  return renderWithProviders(null, {
    route,
    routes: [
      {
        element: <AuthLayout />,
        children: [
          { path: "/signin", element: <SignInPage /> },
          { path: "/signup", element: <SignUpPage /> },
          { path: "/forgot", element: <ForgotPasswordPage /> },
          { path: "/reset", element: <ResetPasswordPage /> },
          { path: "/verify", element: <VerifyEmailPage /> },
        ],
      },
      { path: "/", element: <Home /> },
      { path: "/projects/3/feed", element: <h1>Feed 3</h1> },
      { path: "/settings", element: <h1>Settings</h1> },
    ],
  });
}

describe("safeNext", () => {
  it.each([
    [null, "/"],
    ["/projects/3/feed?paper=2", "/projects/3/feed?paper=2"],
    ["https://evil.example/x", "/"],
    ["//evil.example/x", "/"],
    ["/\\evil.example", "/"],
    ["/signin?next=/x", "/"],
    ["relative/path", "/"],
  ])("%s → %s", (raw, expected) => {
    expect(safeNext(raw)).toBe(expected);
  });
});

describe("SignInPage", () => {
  it("signs in with email and password and continues to `next`", async () => {
    const { requests } = installTestTransport(
      server((request) => (request.path === "/api/auth/signin" ? jsonResponse(makeMe()) : errorResponse(404, "not_found"))),
    );
    const { user } = renderAuth(`/signin?next=${encodeURIComponent("/projects/3/feed")}`);
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), " test-user@example.org ");
    await user.type(screen.getByLabelText("Password"), "correct horse 1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Feed 3" });
    const signIn = requests.find((request) => request.path === "/api/auth/signin");
    expect(signIn?.body).toEqual({ email: "test-user@example.org", password: "correct horse 1" });
    expect(signIn?.headers.get("X-Radar-Client")).toBe("web");
  });

  it("says when the email or password is wrong", async () => {
    installTestTransport(server(() => errorResponse(401, "invalid_credentials", "Wrong email or password")));
    const { user } = renderAuth("/signin");
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), "a@example.org");
    await user.type(screen.getByLabelText("Password"), "wrong password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
  });

  it("explains rate limiting with the wait time", async () => {
    installTestTransport(
      server(() => errorResponse(429, "rate_limited", "Too many attempts", { retry_after_seconds: 600 })),
    );
    const { user } = renderAuth("/signin");
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), "a@example.org");
    await user.type(screen.getByLabelText("Password"), "whatever pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 10 minutes.");
  });

  it("links the enabled providers to the API with `next`, and shows OAuth errors", async () => {
    installTestTransport(server(() => errorResponse(404, "not_found"), makeAuthConfig({ providers: { password: true, google: true, github: false } })));
    renderAuth("/signin?error=identity_in_use&next=%2Fprojects%2F3%2Ffeed");
    const google = await screen.findByRole("link", { name: "Continue with Google" });
    expect(google).toHaveAttribute("href", "/api/auth/oauth/google/start?next=%2Fprojects%2F3%2Ffeed");
    expect(screen.queryByRole("link", { name: "Continue with GitHub" })).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("That account is already linked to another user.");
  });

  it("hides the password form when password login is off", async () => {
    installTestTransport(server(() => errorResponse(404, "not_found"), makeAuthConfig({ providers: { password: false, google: true, github: true } })));
    renderAuth("/signin");
    await screen.findByRole("link", { name: "Continue with GitHub" });
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("offers to resend the verification email after a right password for an unconfirmed account", async () => {
    const { requests } = installTestTransport(
      server((request) => {
        if (request.path === "/api/auth/signin") return errorResponse(403, "email_unverified", "Confirm your email address first");
        if (request.path === "/api/auth/email/resend") return jsonResponse(null, 202);
        return errorResponse(404, "not_found");
      }),
    );
    const { user } = renderAuth(`/signin?next=${encodeURIComponent("/projects/3/feed")}`);
    await screen.findByRole("heading", { name: "Sign in" });
    await user.type(screen.getByLabelText("Email"), "ada@example.org");
    await user.type(screen.getByLabelText("Password"), "correct horse 1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Confirm your email first.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull(); // not shown as "wrong password"
    await user.click(screen.getByRole("button", { name: "Resend email" }));
    expect(await screen.findByText("A new link is on its way to ada@example.org.")).toBeInTheDocument();
    expect(requests.find((request) => request.path === "/api/auth/email/resend")?.body).toEqual({ email: "ada@example.org", next: "/projects/3/feed" });
    expect(screen.getByRole("button", { name: "Resend email" })).toBeDisabled();
    expect(screen.getByText("You can ask for another email in 60 s.")).toBeInTheDocument();
  });

  it("skips the form when already signed in", async () => {
    installTestTransport((request) =>
      request.path === "/api/me" ? jsonResponse(makeMe()) : request.path === "/api/auth/config" ? jsonResponse(makeAuthConfig()) : errorResponse(404, "not_found"),
    );
    renderAuth("/signin?next=%2Fsettings");
    await screen.findByRole("heading", { name: "Settings" });
  });
});

describe("SignUpPage", () => {
  it("checks the password length before sending anything", async () => {
    const { requests } = installTestTransport(server(() => jsonResponse(makeMe(), 201)));
    const { user } = renderAuth("/signup");
    await screen.findByRole("heading", { name: "Create your account" });
    await user.type(screen.getByLabelText("Email"), "new@example.org");
    await user.type(screen.getByLabelText("Password"), "short");
    expect(screen.getByText("5/10")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Use at least 10 characters.")).toBeVisible();
    expect(requests.some((request) => request.path === "/api/auth/signup")).toBe(false);
  });

  it("offers to sign in when the email is taken", async () => {
    installTestTransport(server(() => errorResponse(409, "email_taken", "An account with this email already exists")));
    const { user } = renderAuth("/signup");
    await screen.findByRole("heading", { name: "Create your account" });
    await user.type(screen.getByLabelText("Email"), "taken@example.org");
    await user.type(screen.getByLabelText("Password"), "long enough password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("link", { name: "Sign in instead" })).toHaveAttribute("href", "/signin");
  });

  it("creates the account (name optional) and goes home", async () => {
    const { requests } = installTestTransport(
      server((request) => (request.path === "/api/auth/signup" ? jsonResponse(makeMe(), 201) : errorResponse(404, "not_found"))),
    );
    const { user } = renderAuth("/signup");
    await screen.findByRole("heading", { name: "Create your account" });
    await user.type(screen.getByLabelText(/^Name/), "Ada");
    await user.type(screen.getByLabelText("Email"), "ada@example.org");
    await user.type(screen.getByLabelText("Password"), "long enough password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Home" });
    expect(requests.find((request) => request.path === "/api/auth/signup")?.body).toEqual({
      email: "ada@example.org",
      password: "long enough password",
      display_name: "Ada",
    });
  });
});

describe("SignUpPage with email verification", () => {
  it("shows “Check your inbox” instead of signing in", async () => {
    const { requests } = installTestTransport(
      server(
        (request) =>
          request.path === "/api/auth/signup" ? jsonResponse({ verification_required: true, email: "ada@example.org" }, 202) : errorResponse(404, "not_found"),
        makeAuthConfig({ email_verification: true }),
      ),
    );
    const { user } = renderAuth(`/signup?next=${encodeURIComponent("/projects/3/feed")}`);
    await screen.findByRole("heading", { name: "Create your account" });
    expect(screen.getByText(/We will email you a link to confirm your address/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "Ada@example.org");
    await user.type(screen.getByLabelText("Password"), "long enough password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Check your inbox" });
    expect(screen.getByText("ada@example.org")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resend email" })).toBeDisabled(); // the first email was just sent
    expect(screen.getByText("You can ask for another email in 60 s.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/signin?next=%2Fprojects%2F3%2Ffeed");
    expect(requests.find((request) => request.path === "/api/auth/signup")?.body).toEqual({
      email: "Ada@example.org",
      password: "long enough password",
      next: "/projects/3/feed",
    });
    expect(screen.queryByRole("heading", { name: "Feed 3" })).toBeNull();
  });

  it("offers Google/GitHub instead of the form when the server cannot send the email", async () => {
    installTestTransport(server(() => errorResponse(404, "not_found"), makeAuthConfig({ password_signup: false, email_delivery: false, email_verification: true })));
    renderAuth("/signup");
    expect(await screen.findByRole("link", { name: "Sign up with Google" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign up with GitHub" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.getByText(/Signing up with an email and password is not available on this server/)).toBeInTheDocument();
  });

  it("says sign-up is closed when no provider is left", async () => {
    installTestTransport(
      server(() => errorResponse(404, "not_found"), makeAuthConfig({ password_signup: false, providers: { password: true, google: false, github: false } })),
    );
    renderAuth("/signup");
    await waitFor(() => expect(screen.getByText(/Sign-up is closed on this server/)).toBeInTheDocument());
    expect(screen.queryByLabelText("Password")).toBeNull();
  });
});

describe("ForgotPasswordPage", () => {
  it("always answers the same way", async () => {
    const { requests } = installTestTransport(server(() => jsonResponse(null, 202)));
    const { user } = renderAuth("/forgot");
    await screen.findByRole("heading", { name: "Reset your password" });
    await user.type(screen.getByLabelText("Email"), "someone@example.org");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    await screen.findByRole("heading", { name: "Check your inbox" });
    expect(requests.find((request) => request.path === "/api/auth/password/forgot")?.body).toEqual({ email: "someone@example.org" });
  });

  it("says when email is not set up", async () => {
    installTestTransport(server(() => jsonResponse(null, 202), makeAuthConfig({ email_delivery: false })));
    renderAuth("/forgot");
    // Motion applies the card's `initial` style first and settles it on the next frame.
    await waitFor(() => expect(screen.getByText(/Email is not set up on this server/)).toBeVisible());
    expect(screen.getByRole("button", { name: "Send reset link" })).toBeDisabled();
  });
});

describe("ResetPasswordPage", () => {
  it("rejects a missing token without a request", async () => {
    installTestTransport(server(() => errorResponse(404, "not_found")));
    renderAuth("/reset");
    await waitFor(() => expect(screen.getByText("This reset link is invalid or has expired.")).toBeVisible());
  });

  it("saves the new password and goes home", async () => {
    const { requests } = installTestTransport(
      server((request) => (request.path === "/api/auth/password/reset" ? jsonResponse(makeMe()) : errorResponse(404, "not_found"))),
    );
    const { user } = renderAuth("/reset?token=tok-1");
    await screen.findByRole("heading", { name: "Choose a new password" });
    await user.type(screen.getByLabelText("New password"), "a brand new password");
    await user.click(screen.getByRole("button", { name: "Save and sign in" }));
    await screen.findByRole("heading", { name: "Home" });
    expect(requests.find((request) => request.path === "/api/auth/password/reset")?.body).toEqual({
      token: "tok-1",
      password: "a brand new password",
    });
  });

  it("explains an expired token", async () => {
    installTestTransport(server(() => errorResponse(400, "invalid_token", "This link is invalid or has expired")));
    const { user } = renderAuth("/reset?token=old");
    await screen.findByRole("heading", { name: "Choose a new password" });
    await user.type(screen.getByLabelText("New password"), "a brand new password");
    await user.click(screen.getByRole("button", { name: "Save and sign in" }));
    expect(await screen.findByRole("link", { name: "Request a new link" })).toHaveAttribute("href", "/forgot");
  });
});

describe("VerifyEmailPage", () => {
  it("posts the token once, signs in and continues to `next`", async () => {
    const { requests } = installTestTransport(
      server((request) => (request.path === "/api/auth/email/verify" ? jsonResponse(makeMe()) : errorResponse(404, "not_found"))),
    );
    renderAuth(`/verify?token=v-1&next=${encodeURIComponent("/projects/3/feed")}`);
    await screen.findByRole("heading", { name: "Feed 3" });
    expect(requests.filter((request) => request.path === "/api/auth/email/verify")).toHaveLength(1);
    expect(requests.find((request) => request.path === "/api/auth/email/verify")?.body).toEqual({ token: "v-1" });
  });

  it("goes home when there is no `next`", async () => {
    installTestTransport(server((request) => (request.path === "/api/auth/email/verify" ? jsonResponse(makeMe()) : errorResponse(404, "not_found"))));
    renderAuth("/verify?token=v-1");
    await screen.findByRole("heading", { name: "Home" });
  });

  it("asks for the password when the link was opened in another browser", async () => {
    const { requests } = installTestTransport(
      server((request) => {
        if (request.path !== "/api/auth/email/verify") return errorResponse(404, "not_found");
        const body = request.body as { token: string; password?: string };
        if (body.password === undefined) return errorResponse(401, "password_required", "Enter your password", { email: "ada@example.org" });
        if (body.password !== "correct horse 1") return errorResponse(401, "invalid_credentials", "The password is incorrect");
        return jsonResponse(makeMe());
      }),
    );
    const { user } = renderAuth("/verify?token=v-2");
    await screen.findByRole("heading", { name: "Confirm your email" });
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.org");
    expect(screen.getByLabelText("Email")).toHaveAttribute("readonly");
    expect(screen.getByRole("link", { name: "Reset the password" })).toHaveAttribute("href", "/forgot");
    await user.type(screen.getByLabelText("Password"), "a wrong guess");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    expect(await screen.findByText("Wrong password.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "correct horse 1");
    await user.click(screen.getByRole("button", { name: "Confirm and sign in" }));
    await screen.findByRole("heading", { name: "Home" });
    expect(requests.filter((request) => request.path === "/api/auth/email/verify").map((request) => request.body)).toEqual([
      { token: "v-2" },
      { token: "v-2", password: "a wrong guess" },
      { token: "v-2", password: "correct horse 1" },
    ]);
  });

  it("offers a new link by email when signed out", async () => {
    const { requests } = installTestTransport(
      server((request) => {
        if (request.path === "/api/auth/email/resend") return jsonResponse(null, 202);
        return errorResponse(400, "invalid_token", "This link is invalid or has expired");
      }),
    );
    const { user } = renderAuth("/verify?token=old");
    expect(await screen.findByText("This verification link is invalid or has expired.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "ada@example.org");
    await user.click(screen.getByRole("button", { name: "Send a new link" }));
    expect(await screen.findByText("If an account is waiting for that address, a new link is on its way.")).toBeInTheDocument();
    expect(requests.find((request) => request.path === "/api/auth/email/resend")?.body).toEqual({ email: "ada@example.org" });
  });

  it("offers a new link to a signed-in, unverified user", async () => {
    const unverified = makeMe({ user: { ...makeMe().user, email_verified: false } });
    installTestTransport((request) => {
      if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig());
      if (request.path === "/api/me") return jsonResponse(unverified);
      if (request.path === "/api/auth/email/resend") return jsonResponse(null, 202);
      return errorResponse(400, "invalid_token", "This link is invalid or has expired");
    });
    const { user } = renderAuth("/verify?token=bad");
    await user.click(await screen.findByRole("button", { name: "Send a new link" }));
    expect(await screen.findByText("A new link is on its way to your inbox.")).toBeInTheDocument();
  });
});
