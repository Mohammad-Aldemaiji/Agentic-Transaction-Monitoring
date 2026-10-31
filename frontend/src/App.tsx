import { lazy, Suspense, useEffect, useState } from "react";
import {
  Activity,
  ArrowLeftRight,
  BookOpenCheck,
  LayoutDashboard,
  LogOut,
  Menu,
  Phone,
  Settings2,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import {
  api,
  clearSession,
  hasSession,
  label,
  short,
  rememberSession,
} from "./api";
import { supabase } from "./auth";
import { Brand, ErrorBox, Loading } from "./ui";
import Login from "./Login";
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
  {
    id: "queue",
    name: "Review queue",
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
  const [booth, setBooth] = useState<boolean | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null),
    [loading, setLoading] = useState(hasSession()),
    [error, setError] = useState<unknown>(null),
    [page, setPage] = useState("queue"),
    [caseId, setCaseId] = useState(""),
    [menu, setMenu] = useState(false),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api<{ enabled: boolean }>("/booth/config")
      .then((result) => {
        if (live) setBooth(result.enabled);
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
  async function switchRole(role: Role) {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ identity: Identity; csrf_token: string }>(
        "/booth/role",
        { method: "POST", body: { role } },
      );
      rememberSession(result.csrf_token);
      setIdentity(result.identity);
      setPage(allowedPages(role)[0]?.id ?? "queue");
      setCaseId("");
    } catch (error) {
      setError(error);
    } finally {
      setBusy(false);
    }
  }
  const roleTabs = (
    <div className="booth-toolbar" aria-label="Workspace role selection">
      <div className="button-row">
        {(
          [
            ["ingestion", "Bank feed"],
            ["analyst", "Analyst"],
            ["supervisor", "Supervisor"],
            ["admin", "Administrator"],
          ] as const
        ).map(([role, name]) => (
          <button
            key={role}
            className="secondary"
            disabled={busy}
            aria-pressed={identity?.role === role}
            onClick={() => switchRole(role)}
          >
            {name}
          </button>
        ))}
      </div>
    </div>
  );
  if (loading || booth === null) return <Loading />;
  if (!identity)
    return (
      <Login
        presentation={booth === true}
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
            ? ["ingestion", "analyst", "supervisor"].includes(identity.role)
            : n.roles.includes(identity.role) &&
              (!["policy", "models"].includes(n.id) ||
                identity.role === "admin"),
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
      <aside className={"sidebar " + (menu ? "open" : "")}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-only"
            aria-label="Close navigation"
            onClick={() => setMenu(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">MONITORING WORKSPACE</div>
        <nav aria-label="Main navigation">
          {items.map((n) => (
            <button
              key={n.id}
              className={selected.id === n.id ? "active" : ""}
              aria-current={selected.id === n.id ? "page" : undefined}
              onClick={() => {
                setPage(n.id);
                setCaseId("");
                setMenu(false);
              }}
            >
              <n.icon size={18} />
              {n.name}
              {selected.id === n.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="project-tag">
            <span className="live-dot" />
            <div>
              <strong>Senior project</strong>
              <small>Synthetic-data environment</small>
            </div>
          </div>
          <div className="user-card">
            <span className="avatar">
              {identity.role.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{label(identity.role)}</strong>
              <small>
                {booth
                  ? "Bank operations"
                  : `${identity.team} · ${short(identity.id)}`}
              </small>
            </div>
            <button
              className="icon-button"
              disabled={busy}
              title="Sign out"
              aria-label="Sign out"
              onClick={logout}
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </aside>
      {menu && (
        <button
          className="scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="main-shell">
        {booth && roleTabs}
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-only"
              aria-label="Open navigation"
              onClick={() => setMenu(true)}
            >
              <Menu size={21} />
            </button>
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>{selected.name}</strong>
          </div>
          <div className="topbar-right">
            <span className="environment">
              <span className="live-dot" />
              Sample data
            </span>
            <button
              className="text-button"
              onClick={() => {
                setIdentity(null);
                setCaseId("");
              }}
              title={
                booth ? "Switch workspace role" : "Sign in again for recent MFA"
              }
            >
              <Settings2 size={16} />
              {booth ? "Switch role" : "Re-authenticate"}
            </button>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          <ErrorBox error={error} />
          {identity.sample_traffic_enabled && !booth && <SampleTraffic />}
          {selected.id === "queue" ? (
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
              <Queue openCase={setCaseId} />
            )
          ) : selected.id === "transactions" ? (
            booth ? (
              <BoothFeed
                readOnly={identity.role !== "ingestion"}
                openCase={
                  identity.role === "ingestion"
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
