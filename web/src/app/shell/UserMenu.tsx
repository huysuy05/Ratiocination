/**
 * The signed-in user's menu (UI.md §4): initials avatar, name and email, Settings, Sign out. Hidden
 * in local mode. Avatars are initials only — provider pictures would be third-party requests.
 */
import clsx from "clsx";
import { LogOut, Settings } from "lucide-react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useMe, useSignOut } from "../../api/hooks";
import type { User } from "../../api/types";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "../../components/ui/Menu";
import { Tooltip } from "../../components/ui/Tooltip";
import { paths } from "../paths";
import { AVATAR_COLORS, initials } from "./ProjectSwitcher";

export function UserAvatar({ user, size = 32, className }: { user: Pick<User, "id" | "display_name" | "email">; size?: number; className?: string }) {
  const name = user.display_name || user.email || "?";
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size, backgroundColor: AVATAR_COLORS[user.id % AVATAR_COLORS.length], fontSize: size * 0.38 }}
      className={clsx(
        "grid shrink-0 place-items-center rounded-full font-semibold tracking-tight text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.25),0_0_0_2px_var(--surface)]",
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

export function useSignOutAndLeave(): { signOut: () => void; pending: boolean } {
  const signOut = useSignOut();
  const navigate = useNavigate();
  return {
    pending: signOut.isPending,
    signOut: () =>
      signOut.mutate(undefined, {
        onSuccess: () => void navigate(paths.signIn(), { replace: true }),
        onError: () => {
          toast.error("Could not sign out. Try again.");
        },
      }),
  };
}

export function UserMenu({ variant = "sidebar" }: { variant?: "sidebar" | "rail" | "topbar" }) {
  const { data } = useMe();
  const navigate = useNavigate();
  const { signOut } = useSignOutAndLeave();
  if (!data || data.mode !== "accounts") return null;
  const { user } = data;
  const name = user.display_name || user.email || "Your account";

  const trigger =
    variant === "sidebar" ? (
      <button
        type="button"
        className="group flex w-full items-center gap-2.5 rounded-button px-2 py-1.5 text-left transition-colors hover:bg-surface-2"
      >
        <UserAvatar user={user} />
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-[13px] font-semibold text-text">{name}</span>
          {user.email ? <span className="block truncate text-[11.5px] text-muted">{user.email}</span> : null}
        </span>
      </button>
    ) : (
      <button type="button" aria-label={`Account: ${name}`} className="grid size-11 place-items-center rounded-full">
        <UserAvatar user={user} size={variant === "rail" ? 34 : 30} />
      </button>
    );

  return (
    <Menu>
      {variant === "rail" ? (
        <Tooltip content={name} side="right">
          <MenuTrigger asChild>{trigger}</MenuTrigger>
        </Tooltip>
      ) : (
        <MenuTrigger asChild>{trigger}</MenuTrigger>
      )}
      <MenuContent align={variant === "topbar" ? "end" : "start"} side={variant === "topbar" ? "bottom" : "top"} className="w-64">
        <MenuLabel className="normal-case">
          <span className="block truncate text-[13px] font-semibold tracking-normal text-text">{name}</span>
          {user.email ? <span className="block truncate text-[12px] font-normal tracking-normal text-muted">{user.email}</span> : null}
        </MenuLabel>
        <MenuSeparator />
        <MenuItem icon={<Settings aria-hidden="true" />} onSelect={() => void navigate(paths.settings())}>
          Settings
        </MenuItem>
        <MenuItem icon={<LogOut aria-hidden="true" />} onSelect={signOut}>
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
