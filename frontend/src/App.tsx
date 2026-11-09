import { lazy, Suspense, useEffect, useState } from "react";
import {
  Activity,
  ArrowLeftRight,
  BookOpenCheck,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Phone,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sun,
  X,
} from "lucide-react";
import { api, clearSession, hasSession, label } from "./api";
import { supabase } from "./auth";
import { Brand, ErrorBox, Loading, useTheme } from "./ui";
import Login from "./Login";
import StaffUsers from "./StaffUsers";
import AccountSetup, { hasAccountSetup } from "./AccountSetup";
import Queue from "./Queue";
const CaseWorkspace = lazy(() => import("./CaseWorkspace"));
import Transactions from "./Transactions";
import SampleTraffic from "./SampleTraffic";
import BoothFeed from "./BoothFeed";
import { CustomerCalls } from "./CallPanel";
import Governance, { AuditTrail, Operations } from "./Governance";
const Models = lazy(() => import("./Models"));
import type { Identity, Role } from "./types";
export const navigation = [
  { id: "users", name: "Users", icon: ShieldCheck, roles: ["admin"] },
  {
    id: "queue",
    name: "Overview",
    icon: LayoutDashboard,
    roles: ["analyst", "supervisor", "auditor"],
  },
  {
    id: "transactions",
    name: "Transactions",
    icon: ArrowLeftRight,
    roles: ["ingestion"],
  },
  {
    id: "calls",
    name: "My verification calls",
    icon: Phone,
    roles: ["participant"],
  },
  {
    id: "policy",
    name: "Risk policies",
    icon: ShieldCheck,
    roles: ["admin", "supervisor", "auditor"],
  },
  {
    id: "models",
    name: "Model governance",
    icon: Sparkles,
    roles: ["admin", "supervisor", "auditor"],
  },
  { id: "audit", name: "Audit trail", icon: BookOpenCheck, roles: ["auditor"] },
  { id: "operations", name: "Processing", icon: Activity, roles: ["admin"] },
];
export const allowedPages = (role: Role) =>
  navigation.filter((n) => n.roles.includes(role));
export default function App() {
  const [staffAccess, setStaffAccess] = useState(false);
  const [accountSetup, setAccountSetup] = useState(hasAccountSetup);
  const [booth, setBooth] = useState<boolean | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null),
    [loading, setLoading] = useState(hasSession()),
    [error, setError] = useState<unknown>(null),
    [page, setPage] = useState("queue"),
    [caseId, setCaseId] = useState(""),
    [menu, setMenu] = useState(false),
    [busy, setBusy] = useState(false);
  const { theme, toggle: toggleTheme } = useTheme();
  useEffect(() => {
    let live = true;
    api<{ enabled: boolean; staff_access?: boolean }>("/booth/config")
      .then((result) => {
        if (live) {
          setBooth(result.enabled);
          setStaffAccess(result.staff_access === true);
        }
      })
      .catch(() => {
        if (live) setBooth(false);
      });
    const expire = () => {
      setIdentity(null);
      setCaseId("");
      setError(null);
    };
    window.addEventListener("session-expired", expire);
    if (hasSession())
      api<Identity>("/me")
        .then((me) => {
          if (live) {
            setIdentity(me);
            setPage(allowedPages(me.role)[0]?.id ?? "queue");
          }
        })
        .catch((e) => {
          if (live) setError(e);
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    return () => {
      live = false;
      window.removeEventListener("session-expired", expire);
    };
  }, []);
  async function logout() {
    setBusy(true);
    setError(null);
    try {
      await api("/auth/logout", { method: "POST", body: {} });
      clearSession();
      await supabase?.auth.signOut({ scope: "local" });
      setIdentity(null);
      setCaseId("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (loading || booth === null) return <Loading />;
  if (accountSetup)
    return (
      <AccountSetup
        staffAccess={staffAccess}
        onDone={() => setAccountSetup(false)}
      />
    );
  if (!identity)
    return (
      <Login
        presentation={booth === true}
        staffAccess={staffAccess}
        onLogin={(me) => {
          setIdentity(me);
          setPage(allowedPages(me.role)[0]?.id ?? "queue");
          setError(null);
        }}
      />
    );
  const items = booth
    ? navigation
        .filter((n) =>
          n.id === "transactions"
            ? ["ingestion", "analyst", "supervisor", "admin"].includes(
                identity.role,
              )
            : n.roles.includes(identity.role),
        )
        .map((n) => ({
          ...n,
          name:
            n.id === "policy"
              ? "Risk settings"
              : n.id === "models"
                ? "Scoring models"
                : n.name,
        }))
    : allowedPages(identity.role);
  const selected = items.find((n) => n.id === page) ?? items[0];
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <div className="aurora" aria-hidden="true">
        <span />
        <span />
      </div>
      <header className="topnav">
        <div className="topnav-inner">
          <button
            className="glass-button mobile-only"
            aria-label={menu ? "Close navigation" : "Open navigation"}
            aria-expanded={menu}
            onClick={() => setMenu((m) => !m)}
          >
            {menu ? <X size={18} /> : <Menu size={18} />}
          </button>
          <Brand />
          <nav
            aria-label="Main navigation"
            className={"topnav-links" + (menu ? " open" : "")}
          >
            {items.map((n) => (
              <button
                key={n.id}
                aria-current={selected.id === n.id ? "page" : undefined}
                onClick={() => {
                  setPage(n.id);
                  setCaseId("");
                  setMenu(false);
                }}
              >
                <n.icon size={16} />
                {n.name}
              </button>
            ))}
          </nav>
          <div className="topnav-actions">
            <span className="env-pill" title="Synthetic-data environment">
              <span className="live-dot" />
              Sample data
            </span>
            <button
              className="glass-button"
              onClick={toggleTheme}
              aria-label={
                theme === "dark"
                  ? "Switch to light theme"
                  : "Switch to dark theme"
              }
              title={
                theme === "dark"
                  ? "Switch to light theme"
                  : "Switch to dark theme"
              }
            >
              {theme === "dark" ? (
                <Sun key="sun" className="turn" size={17} />
              ) : (
                <Moon key="moon" className="turn" size={17} />
              )}
            </button>
            <button
              className="glass-button"
              onClick={() => {
                setIdentity(null);
                setCaseId("");
              }}
              title={
                staffAccess
                  ? "Sign in again with your password and face"
                  : "Sign in again for recent MFA"
              }
            >
              <Settings2 size={16} />
              <span className="search-hint">Re-authenticate</span>
            </button>
            <div
              className="user-chip"
              title={"Signed in as " + label(identity.role)}
            >
              <span className="avatar">
                {identity.role.slice(0, 2).toUpperCase()}
              </span>
              <div>
                <strong>{label(identity.role)}</strong>
                <small>{booth ? "Bank operations" : identity.team}</small>
              </div>
            </div>
            <button
              className="glass-button"
              disabled={busy}
              title="Sign out"
              aria-label="Sign out"
              onClick={logout}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>
      <div className="main-shell">
        <main id="main" tabIndex={-1}>
          <ErrorBox error={error} />
          {identity.sample_traffic_enabled && !booth && <SampleTraffic />}
          {selected.id === "users" ? (
            <StaffUsers enabled={staffAccess} />
          ) : selected.id === "queue" ? (
            caseId ? (
              <Suspense fallback={<Loading />}>
                <CaseWorkspace
                  key={caseId}
                  id={caseId}
                  identity={identity}
                  onBack={() => setCaseId("")}
                />
              </Suspense>
            ) : (
              <Queue openCase={setCaseId} me={identity.id} />
            )
          ) : selected.id === "transactions" ? (
            booth ? (
              <BoothFeed
                readOnly={
                  identity.role !== (staffAccess ? "admin" : "ingestion")
                }
                openCase={
                  ["ingestion", "admin"].includes(identity.role)
                    ? undefined
                    : (id) => {
                        setCaseId(id);
                        setPage("queue");
                      }
                }
              />
            ) : (
              <Transactions />
            )
          ) : selected.id === "calls" ? (
            <CustomerCalls identity={identity} />
          ) : selected.id === "policy" ? (
            <Governance identity={identity} />
          ) : selected.id === "models" ? (
            <Suspense fallback={<Loading />}>
              <Models identity={identity} />
            </Suspense>
          ) : selected.id === "audit" ? (
            <AuditTrail />
          ) : (
            <Operations />
          )}
          <footer className="workspace-footer">
            <ShieldCheck size={14} />
            <span>Decisions stay human. Evidence stays traceable.</span>
            <span>Rasid / راصد</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
