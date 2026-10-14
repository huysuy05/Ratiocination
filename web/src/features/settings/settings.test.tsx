import { screen, waitFor, within } from "@testing-library/react";
import { Toaster } from "sonner";
import { describe, expect, it } from "vitest";
import type { Me, SystemSettings } from "../../api/types";
import SettingsPage from "../../pages/SettingsPage";
import { makeAuthConfig, makeJob, makeMe, makeUser } from "../../test/fixtures";
import { errorResponse, installTestTransport, jsonResponse, renderWithProviders, type RecordedRequest } from "../../test/utils";

const SETTINGS: SystemSettings = {
  offline: false,
  privacy_disclosure: "Your projects stay on this server. (test disclosure)",
  embedding: {
    default_model: "fastembed/BAAI/bge-small-en-v1.5",
    active_model: "local/hashing-v1",
    fallback_active: true,
    model_cached: false,
    download_job: null,
    available: [],
  },
  ai_summary: { configured: true, model: "claude-opus-5-5" },
  admin: {
    scheduler: { enabled: true, next_run_at: "2026-10-13T06:30:00Z", cron: "30 6 * * *", timezone: "UTC" },
    connectors: {
      openalex: {
        has_key: true,
        budget: { provider: "openalex", daily_budget_usd: 1, spent_today_usd: 0.12, remaining_today_usd: 0.88, stop_fraction: 0.9, stopped: false, source: "headers" },
      },
      semantic_scholar: { has_key: false, breaker_until: null },
      arxiv: { breaker_until: null },
      smtp: { configured: true },
      oauth: { google: true, github: false },
    },
    users: 3,
    last_daily: null,
  },
  version: "0.3.0",
};

function server(requests: RecordedRequest[], me: Me = makeMe()) {
  let current = me;
  return (request: RecordedRequest) => {
    requests.push(request);
    if (request.path === "/api/me" && request.method === "GET") return jsonResponse(current);
    if (request.path === "/api/me" && request.method === "PATCH") {
      const body = request.body as { display_name?: string; preferences?: Partial<Me["user"]["preferences"]> };
      current = {
        ...current,
        user: {
          ...current.user,
          ...(body.display_name ? { display_name: body.display_name } : {}),
          preferences: { ...current.user.preferences, ...(body.preferences ?? {}) },
        },
      };
      return jsonResponse(current);
    }
    if (request.path === "/api/me/password") {
      const body = request.body as { current_password?: string };
      return body.current_password === "wrong password!" ? errorResponse(401, "invalid_credentials", "Wrong password") : jsonResponse(current);
    }
    if (request.path === "/api/me" && request.method === "DELETE") return new Response(null, { status: 204 });
    if (request.path === "/api/me/identities/github") return errorResponse(409, "conflict", "Last sign-in method");
    if (request.path === "/api/auth/config") return jsonResponse(makeAuthConfig({ providers: { password: true, google: true, github: true } }));
    if (request.path === "/api/settings" && request.method === "GET") return jsonResponse(SETTINGS);
    if (request.path === "/api/settings" && request.method === "PATCH") return jsonResponse(SETTINGS);
    if (request.path === "/api/jobs/run") return jsonResponse({ job: makeJob({ id: 9, kind: "daily", status: "running", project_id: null }) }, 202);
    if (request.path === "/api/jobs/9") return jsonResponse(makeJob({ id: 9, kind: "daily", status: "running", project_id: null, progress: 0.3 }));
    return errorResponse(404, "not_found", `not mocked ${request.path}`);
  };
}

function renderSettings() {
  return renderWithProviders(null, {
    route: "/settings",
    routes: [
      {
        path: "/settings",
        element: (
          <>
            <SettingsPage />
            <Toaster />
          </>
        ),
      },
      { path: "/signin", element: <h1>Sign in page</h1> },
    ],
  });
}

describe("SettingsPage", () => {
  it("renames the account and reports a wrong current password", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests));
    const { user } = renderSettings();
    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Ada");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.find((request) => request.method === "PATCH" && request.path === "/api/me")?.body).toEqual({ display_name: "Ada" }));

    await user.type(screen.getByLabelText("Current password"), "wrong password!");
    await user.type(screen.getByLabelText("New password"), "a new long password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("That is not your current password.")).toBeInTheDocument();
  });

  it("sets a first password without asking for the current one", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests, makeMe({ user: makeUser({ has_password: false }) })));
    const { user } = renderSettings();
    await user.type(await screen.findByLabelText("New password"), "a new long password");
    expect(screen.queryByLabelText("Current password")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Set password" }));
    await waitFor(() => expect(requests.find((request) => request.path === "/api/me/password")?.body).toEqual({ new_password: "a new long password" }));
  });

  it("connects providers through the API and refuses to remove the last sign-in method", async () => {
    const requests: RecordedRequest[] = [];
    const me = makeMe({ user: makeUser({ has_password: false, identities: [{ provider: "github", email: "gh@example.org", created_at: "2026-10-01T00:00:00Z" }] }) });
    installTestTransport(server(requests, me));
    const { user } = renderSettings();
    expect(await screen.findByRole("link", { name: "Connect" })).toHaveAttribute("href", "/api/auth/oauth/google/start?next=%2Fsettings&link=1");
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Keep at least one way to sign in.")).toBeInTheDocument();
  });

  it("deletes the account only after the email is typed", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests));
    const { user, router } = renderSettings();
    await user.click(await screen.findByRole("button", { name: "Delete account" }));
    const dialog = await screen.findByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: "Delete my account" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "test-user@example.org");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(router.state.location.pathname).toBe("/signin"));
    expect(requests.find((request) => request.method === "DELETE" && request.path === "/api/me")?.body).toEqual({ confirm_email: "test-user@example.org" });
  });

  it("saves preferences", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests));
    const { user } = renderSettings();
    await user.selectOptions(await screen.findByRole("combobox", { name: /Time zone/ }), "Asia/Tokyo");
    await waitFor(() => expect(requests.find((request) => request.method === "PATCH" && request.path === "/api/me")?.body).toEqual({ preferences: { timezone: "Asia/Tokyo" } }));
    await user.click(screen.getByRole("switch", { name: /Load citations automatically/ }));
    await waitFor(() =>
      expect(requests.filter((request) => request.method === "PATCH" && request.path === "/api/me").map((request) => request.body)).toContainEqual({
        preferences: { auto_expand: false },
      }),
    );
  });

  it("lets admins pause the schedule and run the daily job", async () => {
    const requests: RecordedRequest[] = [];
    installTestTransport(server(requests));
    const { user } = renderSettings();
    expect(await screen.findByText("Your projects stay on this server. (test disclosure)")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: /Daily schedule/ }));
    await waitFor(() => expect(requests.find((request) => request.method === "PATCH" && request.path === "/api/settings")?.body).toEqual({ scheduler_enabled: false }));
    await user.click(screen.getByRole("button", { name: "Run daily now" }));
    await waitFor(() => expect(requests.find((request) => request.path === "/api/jobs/run")?.body).toEqual({ kind: "daily" }));
    expect(screen.getByRole("button", { name: /Download model/ })).toBeInTheDocument();
  });

  it("explains single-user mode", async () => {
    installTestTransport(server([], makeMe({ mode: "local" })));
    renderSettings();
    expect(await screen.findByText(/This server runs for one person without sign-in/)).toBeInTheDocument();
  });
});
