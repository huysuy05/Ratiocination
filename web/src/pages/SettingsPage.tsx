/** Settings (UI.md §13): account, preferences, appearance, privacy, similarity model, administration. */
import { Monitor } from "lucide-react";
import { useMe, useSettings } from "../api/hooks";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { AccountSection } from "../features/settings/AccountSection";
import { SettingsCard } from "../features/settings/SettingsCard";
import { AdminSection, AppearanceSection, EmbeddingSection, PreferencesSection, PrivacySection } from "../features/settings/SystemSections";

export default function SettingsPage() {
  const me = useMe();
  const settings = useSettings();
  return (
    <section aria-labelledby="page-title" className="mx-auto flex w-full max-w-3xl flex-col gap-5">
      <PageHeader title="Settings" className="!mb-1" />
      {me.data ? (
        me.data.mode === "accounts" ? (
          <AccountSection user={me.data.user} />
        ) : (
          <SettingsCard title="Single-user mode">
            <p className="flex items-start gap-2.5 text-[14px] text-muted">
              <Monitor aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
              This server runs for one person without sign-in. Set RADAR_AUTH=accounts to let people sign in with Google, GitHub or email.
            </p>
          </SettingsCard>
        )
      ) : (
        <Skeleton className="h-40 rounded-card" />
      )}
      {me.data ? <PreferencesSection user={me.data.user} /> : null}
      <AppearanceSection />
      {settings.data ? (
        <>
          <PrivacySection settings={settings.data} />
          <EmbeddingSection settings={settings.data} admin={me.data?.user.is_admin ?? false} />
          {settings.data.admin ? <AdminSection admin={settings.data.admin} /> : null}
        </>
      ) : settings.isError ? (
        <p role="alert" className="text-[13px] text-not-relevant">
          {settings.error.message}
        </p>
      ) : (
        <Skeleton className="h-40 rounded-card" />
      )}
      {settings.data ? <p className="text-center text-[12px] text-faint">Ratiocination {settings.data.version}</p> : null}
    </section>
  );
}
