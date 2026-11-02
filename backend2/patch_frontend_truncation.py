from __future__ import annotations
import re, shutil
from datetime import datetime
from pathlib import Path

TARGET = Path('generation/frontend_generator.py')
HELPER = r'''

def _fallback_ui_components(target: dict) -> str:
    """Return a compact JSX UI module when the LLM repeatedly truncates it."""
    names = [str(x) for x in (target.get("named_exports") or [])
             if re.fullmatch(r"[A-Za-z_$][\w$]*", str(x))]
    default_export = target.get("default_export")
    lines = ['import React from "react";', '',
             'const baseStyle = { boxSizing: "border-box", fontFamily: "inherit" };', '']
    for name in names:
        low = name.lower()
        if "button" in low:
            args = "{ children, ...props }"
            body = '<button {...props} style={{ ...baseStyle, padding: "0.6rem 1rem", borderRadius: "0.5rem", cursor: "pointer", ...props.style }}>{children}</button>'
        elif "input" in low or "field" in low:
            args = "{ ...props }"
            body = '<input {...props} style={{ ...baseStyle, padding: "0.65rem 0.8rem", border: "1px solid #cbd5e1", borderRadius: "0.5rem", width: "100%", ...props.style }} />'
        elif "badge" in low or "tag" in low:
            args = "{ children, ...props }"
            body = '<span {...props} style={{ ...baseStyle, display: "inline-flex", padding: "0.2rem 0.55rem", borderRadius: "999px", background: "#e2e8f0", ...props.style }}>{children}</span>'
        elif "spinner" in low or "loading" in low:
            args = "{ children, ...props }"
            body = '<span role="status" aria-label="Loading" {...props}>{children || "Loading…"}</span>'
        elif "modal" in low or "dialog" in low:
            args = "{ open = true, children, ...props }"
            body = 'open ? <div role="dialog" aria-modal="true" {...props} style={{ ...baseStyle, position: "fixed", inset: 0, zIndex: 1000, background: "rgba(15,23,42,.45)", display: "grid", placeItems: "center", ...props.style }}><section style={{ background: "white", color: "#0f172a", padding: "1.25rem", borderRadius: "0.75rem" }}>{children}</section></div> : null'
        else:
            args = "{ children, ...props }"
            body = '<div {...props} style={{ ...baseStyle, ...props.style }}>{children}</div>'
        lines.append(f'export function {name}({args}) {{ return {body}; }}')
        lines.append('')
    if default_export and re.fullmatch(r"[A-Za-z_$][\w$]*", str(default_export)):
        if default_export in names:
            lines.append(f'export default {default_export};')
        else:
            lines.append('export default function ' + str(default_export) + '({ children, ...props }) { return <div {...props} style={{ ...baseStyle, ...props.style }}>{children}</div>; }')
    elif not names:
        lines.append('export default function UIComponents({ children, ...props }) { return <div {...props}>{children}</div>; }')
    return '\n'.join(lines).rstrip() + '\n'
'''

def main():
    if not TARGET.is_file():
        raise SystemExit('Run this script from the AgentForge backend2 project root.')
    source = TARGET.read_text(encoding='utf-8')
    if 'def _fallback_ui_components(' in source:
        print('Truncation fallback already installed; no changes made.')
        return
    marker = '\nasync def _generate_file('
    if marker not in source:
        raise SystemExit('Could not find _generate_file; file unchanged.')
    backup = TARGET.with_name(f'frontend_generator.py.bak-{datetime.now():%Y%m%d-%H%M%S}')
    shutil.copy2(TARGET, backup)
    source = source.replace(marker, HELPER + marker, 1)
    source, n = re.subn(r'for attempt in range\(2\):', 'for attempt in range(3):', source, count=1)
    if n != 1:
        raise SystemExit(f'Expected one generation retry loop, found {n}; backup created, source not written.')
    old_retry = '''        if attempt == 1:
            current_prompt += (
                "\\n\\nRETRY: Your previous output was truncated. "
                "Generate a much smaller but complete implementation. "
                "Remove decorative details and unnecessary helpers. "
                "Preserve all required exports and working core behavior."
            )'''
    new_retry = '''        if attempt == 1:
            current_prompt += (
                "\\n\\nRETRY: Your previous output was truncated. "
                "Return a compact complete implementation with only required exports and core behavior."
            )
        elif attempt == 2:
            current_prompt = (
                "Generate the smallest complete JavaScript/JSX implementation for this file. "
                "No explanation or markdown. Preserve every required export.\\n\\n"
                + _file_context(plan, manifest, target, api_contract=api_contract)
            )'''
    if old_retry not in source:
        raise SystemExit('Could not find retry prompt; backup created, source not written.')
    source = source.replace(old_retry, new_retry, 1)
    old_tokens = 'max_tokens=FILE_MAX_TOKENS if attempt == 0 else 1800,'
    if old_tokens not in source:
        raise SystemExit('Could not find max_tokens setting; backup created, source not written.')
    source = source.replace(old_tokens, 'max_tokens=FILE_MAX_TOKENS if attempt == 0 else (1200 if attempt == 1 else 800),', 1)
    old_handler = '''            if (
                "output was truncated" in str(exc).lower()
                and attempt == 0
            ):
                print(
                    f"Output truncated for {target['path']}; "
                    "retrying with a shorter implementation..."
                )
                continue
            raise'''
    new_handler = '''            if "output was truncated" in str(exc).lower() and attempt < 2:
                print(
                    f"Output truncated for {target['path']}; "
                    f"retrying with a smaller prompt (attempt {attempt + 2}/3)..."
                )
                continue
            if "output was truncated" in str(exc).lower() and Path(target["path"]).name.lower() == "uicomponents.jsx":
                print(f"LLM repeatedly truncated {target['path']}; using deterministic compact UI fallback.")
                return _fallback_ui_components(target)
            raise'''
    if old_handler not in source:
        raise SystemExit('Could not find truncation handler; backup created, source not written.')
    source = source.replace(old_handler, new_handler, 1)
    TARGET.write_text(source, encoding='utf-8')
    print(f'Installed progressive truncation handling in {TARGET}')
    print(f'Backup saved to {backup}')
    print('Next: python -m py_compile generation/frontend_generator.py')
    print('Then: python -m pytest -q')

if __name__ == '__main__':
    main()
