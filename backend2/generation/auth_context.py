"""Create the auth context when pages call ``useAuth()`` but nothing defines it.

The planner decides that Login / Signup / Navbar use ``useAuth()``, but the context file is a
separate generated file and sometimes it is simply never produced (or it exports a different
name). The debugger used to "fix" this by mounting providers, found nothing to mount, and
reported success five times in a row. A provider is deterministic boilerplate, so it is written
here instead of asking a model to guess it.

The generated provider talks to the generated backend exactly as it behaves:
``POST /api/auth/login`` -> ``{ access_token, token_type, expires_in, user }``,
``POST /api/auth/register`` -> the public user (no token), ``GET /api/auth/me``.
"""
from __future__ import annotations

import re
from pathlib import Path

HOOK = "useAuth"
CONTEXT_FILE = Path("src") / "context" / "AuthContext.jsx"

AUTH_CONTEXT = '''import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL || "").replace(/\\/$/, "");
const TOKEN_KEY = "token";
const USER_KEY = "user";

function readStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    if (value === null || value === undefined) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage can be unavailable (private mode); the session then lasts until reload */
  }
}

function readStoredUser() {
  try {
    const raw = readStorage(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function request(path, options = {}, token = null) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === "string" ? detail : "Something went wrong. Please try again.");
  }
  return data;
}

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(readStoredUser);
  const [token, setToken] = useState(() => readStorage(TOKEN_KEY));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const saveSession = useCallback((nextToken, nextUser) => {
    setToken(nextToken);
    setUser(nextUser);
    writeStorage(TOKEN_KEY, nextToken);
    writeStorage(USER_KEY, nextUser ? JSON.stringify(nextUser) : null);
  }, []);

  const clearSession = useCallback(() => saveSession(null, null), [saveSession]);

  // Accept login({ email, password }) and login(email, password).
  const login = useCallback(
    async (emailOrCredentials, maybePassword) => {
      const credentials =
        emailOrCredentials && typeof emailOrCredentials === "object"
          ? emailOrCredentials
          : { email: emailOrCredentials, password: maybePassword };
      setLoading(true);
      setError(null);
      try {
        const data = await request("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: credentials.email, password: credentials.password }),
        });
        saveSession(data.access_token, data.user);
        return data.user;
      } catch (err) {
        setError(err.message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [saveSession]
  );

  // Accept register({ name, email, password }) and register(name, email, password).
  // The backend returns the new user without a token, so the user is logged in right after.
  const register = useCallback(
    async (nameOrPayload, maybeEmail, maybePassword) => {
      const payload =
        nameOrPayload && typeof nameOrPayload === "object"
          ? nameOrPayload
          : { name: nameOrPayload, email: maybeEmail, password: maybePassword };
      setLoading(true);
      setError(null);
      try {
        const created = await request("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({ name: payload.name, email: payload.email, password: payload.password }),
        });
        try {
          const data = await request("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({ email: payload.email, password: payload.password }),
          });
          saveSession(data.access_token, data.user);
        } catch {
          /* the account exists; the user can log in manually */
        }
        return created;
      } catch (err) {
        setError(err.message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [saveSession]
  );

  const logout = useCallback(async () => {
    const current = token;
    clearSession();
    if (current) {
      try {
        await request("/api/auth/logout", { method: "POST" }, current);
      } catch {
        /* the local session is already cleared */
      }
    }
  }, [token, clearSession]);

  // Re-validate a stored session once on load.
  useEffect(() => {
    if (!token) return undefined;
    let cancelled = false;
    request("/api/auth/me", {}, token)
      .then((me) => {
        if (!cancelled && me) {
          setUser(me);
          writeStorage(USER_KEY, JSON.stringify(me));
        }
      })
      .catch(() => {
        if (!cancelled) clearSession();
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(
    () => ({
      user,
      currentUser: user,
      token,
      role: user ? user.role : null,
      isAuthenticated: Boolean(user),
      isLoggedIn: Boolean(user),
      isAdmin: Boolean(user && user.role === "admin"),
      loading,
      error,
      login,
      signIn: login,
      register,
      signup: register,
      signUp: register,
      logout,
      signOut: logout,
      setUser,
    }),
    [user, token, loading, error, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

export default AuthContext;
'''


def _files(frontend_dir: Path) -> dict[str, Path]:
    src = Path(frontend_dir) / "src"
    if not src.is_dir():
        return {}
    return {p.relative_to(src).as_posix(): p for p in sorted(src.rglob("*"))
            if p.is_file() and p.suffix in {".js", ".jsx"}
            and "node_modules" not in p.parts}


def exports_hook(text: str, hook: str = HOOK) -> bool:
    """True when this source exports `hook` (declaration, export block or default export)."""
    if re.search(rf"export\s+(?:default\s+)?(?:async\s+)?(?:function|const)\s+{hook}\b", text):
        return True
    if re.search(rf"export\s+default\s+{hook}\b", text):
        return True
    return any(re.split(r"\s+as\s+", part.strip())[-1].strip() == hook
               for block in re.findall(r"export\s*\{([^}]*)\}", text) for part in block.split(","))


def ensure_auth_context(frontend_dir: Path) -> list[str]:
    """Make `useAuth` exist when something calls it. Returns the files created / changed."""
    files = _files(frontend_dir)
    texts = {label: p.read_text(encoding="utf-8", errors="ignore") for label, p in files.items()}
    users = [label for label, text in texts.items()
             if re.search(rf"\b{HOOK}\s*\(", text) and not exports_hook(text)]
    if not users or any(exports_hook(text) for text in texts.values()):
        return []

    # a context file exists but forgot to export the hook: add it
    for label, text in texts.items():
        if not label.startswith("context/"):
            continue
        ctx = re.search(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:React\.)?createContext\s*\(", text)
        if ctx and re.search(r"AuthProvider\b", text):
            addition = (
                '\nimport { useContext as useContextFromReact } from "react";\n'
                f"export function {HOOK}() {{\n  return useContextFromReact({ctx.group(1)});\n}}\n")
            files[label].write_text(text.rstrip() + "\n" + addition, encoding="utf-8")
            return [f"src/{label}"]

    target = Path(frontend_dir) / CONTEXT_FILE
    if target.exists():
        return []  # something else is there; leave it to the model
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(AUTH_CONTEXT, encoding="utf-8")
    return [CONTEXT_FILE.as_posix()]
