/** Navigation model for the AppShell (UI.md §4). */
import {
  FileText,
  FolderKanban,
  LayoutDashboard,
  Library,
  Radar,
  Settings,
  Settings2,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { paths } from "./paths";

export interface ProjectNavItem {
  key: "feed" | "library" | "graph" | "documents" | "project-settings";
  label: string;
  icon: LucideIcon;
  to: (projectId: number) => string;
}

export interface GlobalNavItem {
  key: "dashboard" | "projects" | "settings";
  label: string;
  icon: LucideIcon;
  to: string;
}

export const PROJECT_NAV: readonly ProjectNavItem[] = [
  { key: "feed", label: "Feed", icon: Radar, to: paths.feed },
  { key: "library", label: "Library", icon: Library, to: paths.library },
  { key: "graph", label: "Graph", icon: Waypoints, to: (id) => paths.graph(id) },
  { key: "documents", label: "Documents", icon: FileText, to: paths.documents },
  { key: "project-settings", label: "Project settings", icon: Settings2, to: paths.projectSettings },
];

export const DASHBOARD_NAV: GlobalNavItem = { key: "dashboard", label: "Dashboard", icon: LayoutDashboard, to: paths.dashboard() };
export const PROJECTS_NAV: GlobalNavItem = { key: "projects", label: "Projects", icon: FolderKanban, to: paths.projects() };
export const SETTINGS_NAV: GlobalNavItem = { key: "settings", label: "Settings", icon: Settings, to: paths.settings() };

/** Workspace section of the sidebar, in order. */
export const GLOBAL_NAV: readonly GlobalNavItem[] = [DASHBOARD_NAV, PROJECTS_NAV, SETTINGS_NAV];

/** < 768 px bottom tabs: Feed, Library, Graph (+ Dashboard and More). */
export const MOBILE_TAB_KEYS = ["feed", "library", "graph"] as const;

/** Project items in the mobile "More" sheet. */
export const MOBILE_MORE_PROJECT_KEYS = ["documents", "project-settings"] as const;
