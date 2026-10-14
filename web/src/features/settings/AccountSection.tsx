/**
 * Account settings (UI.md §13): profile, email verification, password, connected Google/GitHub
 * accounts, signing out other devices and deleting the account.
 */
import { BadgeCheck, KeyRound, Link2, LogOut, Trash2, Unlink } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { errorMessage, getFieldErrors, isApiError } from "../../api/client";
import {
  oauthStartUrl,
  useAuthConfig,
  useChangePassword,
  useDeleteAccount,
  useResendVerification,
  useRevokeOtherSessions,
  useUnlinkIdentity,
  useUpdateMe,
} from "../../api/hooks";
import type { OAuthProvider, User } from "../../api/types";
import { paths } from "../../app/paths";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field, Input } from "../../components/ui/Input";
import { formatDate } from "../../lib/format";
import { MIN_PASSWORD } from "../auth/lib";
import { PasswordInput } from "../auth/PasswordInput";
import { SettingsCard } from "./SettingsCard";

const PROVIDERS: { id: OAuthProvider; label: string }[] = [
  { id: "google", label: "Google" },
  { id: "github", label: "GitHub" },
];

function Profile({ user }: { user: User }) {
  const update = useUpdateMe();
  const resend = useResendVerification();
  const [name, setName] = useState(user.display_name);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update.mutate({ display_name: name.trim() }, { onSuccess: () => toast.success("Name saved"), onError: (e) => toast.error(errorMessage(e)) });
  };
  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
        <Field label="Name" className="min-w-56 flex-1">
          <Input maxLength={100} value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" />
        </Field>
        <Button type="submit" disabled={!name.trim() || name.trim() === user.display_name} loading={update.isPending}>
          Save
        </Button>
      </form>
      <div className="flex flex-wrap items-center gap-2 text-[14px]">
        <span className="text-muted">Email</span>
        <span className="font-medium text-text">{user.email ?? "—"}</span>
        {user.email ? (
          user.email_verified ? (
            <Badge tone="relevant" size="sm" icon={<BadgeCheck aria-hidden="true" />}>
              Verified
            </Badge>
          ) : (
            <>
              <Badge tone="save" size="sm" variant="dashed">
                Not verified
              </Badge>
              <Button
                size="sm"
                variant="ghost"
                loading={resend.isPending}
                onClick={() =>
                  resend.mutate(undefined, {
                    onSuccess: () => toast.success("Verification link sent"),
                    onError: (error) => toast.error(errorMessage(error, "Could not send the link")),
                  })
                }
              >
                Resend link
              </Button>
            </>
          )
        ) : null}
        <span className="ml-auto text-[12px] text-faint">Member since {formatDate(user.created_at)}</span>
      </div>
    </div>
  );
}

function Password({ user }: { user: User }) {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [tooShort, setTooShort] = useState(false);
  const wrongCurrent = isApiError(change.error) && change.error.code === "invalid_credentials";
  const fields = Object.fromEntries(getFieldErrors(change.error).map((field) => [field.path, field.message]));
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (next.length < MIN_PASSWORD) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    change.mutate(
      { ...(user.has_password ? { current_password: current } : {}), new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          toast.success(user.has_password ? "Password changed — other devices were signed out" : "Password set");
        },
      },
    );
  };
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <p className="text-[13px] text-muted">
        {user.has_password ? "Changing it signs you out on every other device." : "Add a password to sign in with your email as well."}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {user.has_password ? (
          <Field label="Current password" error={wrongCurrent ? "That is not your current password." : fields.current_password}>
            <PasswordInput autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
          </Field>
        ) : null}
        <Field label="New password" error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : fields.new_password}>
          <PasswordInput autoComplete="new-password" meter value={next} onChange={(event) => setNext(event.target.value)} />
        </Field>
      </div>
      <div>
        <Button type="submit" leadingIcon={<KeyRound aria-hidden="true" />} loading={change.isPending} disabled={!next || (user.has_password && !current)}>
          {user.has_password ? "Change password" : "Set password"}
        </Button>
      </div>
    </form>
  );
}

function Identities({ user }: { user: User }) {
  const config = useAuthConfig();
  const unlink = useUnlinkIdentity();
  const providers = PROVIDERS.filter((provider) => config.data?.providers[provider.id] || user.identities.some((i) => i.provider === provider.id));
  if (providers.length === 0) return <p className="text-[13px] text-muted">No sign-in providers are set up on this server.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {providers.map(({ id, label }) => {
        const identity = user.identities.find((i) => i.provider === id);
        return (
          <li key={id} className="flex flex-wrap items-center gap-3 rounded-control border border-border bg-surface-2/40 px-3.5 py-2.5">
            <span className="w-20 text-[14px] font-medium text-text">{label}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{identity ? (identity.email ?? "Connected") : "Not connected"}</span>
            {identity ? (
              <Button
                size="sm"
                variant="ghost"
                leadingIcon={<Unlink aria-hidden="true" />}
                loading={unlink.isPending && unlink.variables === id}
                onClick={() =>
                  unlink.mutate(id, {
                    onSuccess: () => toast.success(`${label} disconnected`),
                    onError: (error) =>
                      toast.error(isApiError(error) && error.code === "conflict" ? "Keep at least one way to sign in." : errorMessage(error)),
                  })
                }
              >
                Disconnect
              </Button>
            ) : config.data?.providers[id] ? (
              <a href={oauthStartUrl(id, paths.settings(), true)} className={buttonClassName({ size: "sm", variant: "secondary" })}>
                <Link2 aria-hidden="true" />
                Connect
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function DangerZone({ user }: { user: User }) {
  const revoke = useRevokeOtherSessions();
  const remove = useDeleteAccount();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13.5px] text-muted">Signed in somewhere you no longer use? End every other session.</p>
        <Button
          leadingIcon={<LogOut aria-hidden="true" />}
          loading={revoke.isPending}
          onClick={() =>
            revoke.mutate(undefined, {
              onSuccess: (result) => toast.success(result.revoked === 0 ? "No other session was open" : `Signed out ${result.revoked} other session${result.revoked === 1 ? "" : "s"}`),
            })
          }
        >
          Sign out other devices
        </Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-[13.5px] text-muted">Delete your account with all projects, ratings, documents and summaries.</p>
        <Button variant="danger" leadingIcon={<Trash2 aria-hidden="true" />} onClick={() => setConfirming(true)}>
          Delete account
        </Button>
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={(open) => {
          setConfirming(open);
          if (!open) setTyped("");
        }}
        tone="danger"
        title="Delete your account?"
        description="Everything you stored here is deleted for good. Shared public paper records stay."
        confirmLabel="Delete my account"
        confirmDisabled={typed.trim().toLowerCase() !== (user.email ?? "").toLowerCase()}
        pending={remove.isPending}
        onConfirm={() =>
          remove.mutateAsync({ confirm_email: typed.trim() }).then(
            () => void navigate(paths.signIn(), { replace: true }),
            (error: unknown) => toast.error(errorMessage(error, "Could not delete the account")),
          )
        }
      >
        <Field label={`Type ${user.email ?? "your email"} to confirm`}>
          <Input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" />
        </Field>
      </ConfirmDialog>
    </div>
  );
}

export function AccountSection({ user }: { user: User }) {
  return (
    <>
      <SettingsCard title="Account" description="Who you are on this server.">
        <Profile user={user} />
      </SettingsCard>
      <SettingsCard title="Password">
        <Password user={user} />
      </SettingsCard>
      <SettingsCard title="Connected accounts" description="Sign in with Google or GitHub in addition to your password.">
        <Identities user={user} />
      </SettingsCard>
      <SettingsCard title="Sessions and data">
        <DangerZone user={user} />
      </SettingsCard>
    </>
  );
}
