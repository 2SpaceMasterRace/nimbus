"""BYOK setup validation and onboarding pages for Nimbus Slack tenants."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import TYPE_CHECKING

from nimbus_slack.store import TenantConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

_AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

# Curated prompt ideas surfaced on the post-setup onboarding page. Grouped by
# capability area so the More Ideas section reads as a quick-reference card.
_ONBOARDING_PROMPTS: tuple[str, ...] = (
    # File backup & sync
    "Save all files in this channel to S3.",
    "Which channel files are missing from S3?",
    "Which files changed since last sync?",
    "Back up files from every channel I'm in.",
    # Discovery & intelligence
    "Summarize all PDFs shared in this project channel this week.",
    "Find the latest pricing deck and explain how it differs from the old one.",
    "Find files containing secrets, credentials, PII, or risky data.",
    "List all spreadsheets shared in the last 30 days.",
    # Housekeeping
    "Detect duplicate or stale files.",
    "Which files are too large to preview in Slack?",
    "Which saved files have never been downloaded?",
    # Workspace health
    "Show me the workspace health summary.",
    "Are there any pending approvals?",
    "What tasks are currently running?",
)

# Canonical first prompt that exercises the adapter-owned file pipeline.
_FIRST_PROMPT = "@Nimbus what files in this channel are not saved in my s3 bucket?"


class TenantSetupError(ValueError):
    """Raised when a BYOK setup payload is invalid."""


@dataclass(frozen=True, slots=True)
class TenantSetupInput:
    """Validated setup input submitted through the secure setup page."""

    openrouter_api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket: str
    s3_prefix: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> TenantSetupInput:
        """Validate raw JSON or form data."""
        prefix = _optional_text(payload, "s3_prefix").strip("/")
        return cls(
            openrouter_api_key=_required_secret(payload, "openrouter_api_key"),
            aws_access_key_id=_required_secret(payload, "aws_access_key_id"),
            aws_secret_access_key=_required_secret(payload, "aws_secret_access_key"),
            aws_region=_required_region(payload, "aws_region"),
            s3_bucket=_required_bucket(payload, "s3_bucket"),
            s3_prefix=prefix,
        )

    def to_tenant_config(self, *, team_id: str) -> TenantConfig:
        """Convert setup input into a persistent tenant configuration."""
        now = datetime.now(UTC)
        return TenantConfig(
            team_id=team_id,
            openrouter_api_key=self.openrouter_api_key,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            aws_region=self.aws_region,
            s3_bucket=self.s3_bucket,
            s3_prefix=self.s3_prefix,
            status="configured",
            updated_at=now,
            validated_at=None,
        )


# Inline script body: reveal toggles + sessionStorage form-draft persistence.
# Secret fields (openrouter_api_key, aws_access_key_id, aws_secret_access_key)
# are saved to sessionStorage just before the form submits so they survive the
# POST → 400 re-render cycle without ever appearing in the HTML source.
# sessionStorage is tab-scoped and clears when the tab closes, so the draft
# cannot leak to other origins or sessions.
_SCRIPT_BODY = """
(function () {
  var DRAFT_KEY = 'nimbus_setup_draft';
  var SECRET_IDS = ['openrouter_api_key', 'aws_access_key_id', 'aws_secret_access_key'];

  /* Restore secret fields from a previous failed submit */
  (function restoreDraft() {
    try {
      var raw = sessionStorage.getItem(DRAFT_KEY);
      if (!raw) { return; }
      var draft = JSON.parse(raw);
      SECRET_IDS.forEach(function (id) {
        var el = document.getElementById(id);
        if (el && draft[id] && !el.value) { el.value = draft[id]; }
      });
    } catch (ignore) {}
  })();

  /* Save secret fields immediately before the form is submitted */
  var form = document.querySelector('form');
  if (form) {
    form.addEventListener('submit', function () {
      try {
        var draft = {};
        SECRET_IDS.forEach(function (id) {
          var el = document.getElementById(id);
          if (el && el.value) { draft[id] = el.value; }
        });
        sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      } catch (ignore) {}
    });
  }

  /* Reveal / hide password toggle */
  document.querySelectorAll('[data-reveal-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = document.getElementById(btn.dataset.revealTarget);
      if (!target) { return; }
      var showing = target.type === 'text';
      target.type = showing ? 'password' : 'text';
      btn.setAttribute('aria-pressed', showing ? 'false' : 'true');
      btn.setAttribute(
        'aria-label',
        showing ? 'Show password' : 'Hide password'
      );
    });
  });
})();
"""

_REVEAL_SCRIPT = f"<script>{_SCRIPT_BODY}</script>"

_EYE_SVG = (
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/>'
    '<circle cx="12" cy="12" r="3"/>'
    '<path class="reveal-slash" d="M4 4l16 16"/>'
    "</svg>"
)


def _reveal_button(target_id: str) -> str:
    """Render an icon-only show/hide toggle for one credential input."""
    return (
        f'<button type="button" class="reveal" '
        f'data-reveal-target="{target_id}" '
        f'aria-pressed="false" aria-label="Show password">'
        f"{_EYE_SVG}</button>"
    )


def _workspace_tag_html(team_id: str, team_name: str | None = None) -> str:
    """Render the workspace identity pill shown in the setup form header."""
    escaped_id = escape(team_id)
    if team_name:
        escaped_name = escape(team_name)
        return (
            '<div class="workspace-tag">'
            '<span class="workspace-tag__label">Workspace</span>'
            f'<span class="workspace-tag__value">{escaped_name}</span>'
            f'<span class="workspace-tag__id">[{escaped_id}]</span>'
            "</div>"
        )
    return (
        '<div class="workspace-tag">'
        '<span class="workspace-tag__label">Workspace</span>'
        f'<span class="workspace-tag__value">{escaped_id}</span>'
        "</div>"
    )


def _csp_sha256(content: str) -> str:
    """Return the CSP source-list literal for an inline script or style body."""
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


_INLINE_SCRIPT_CSP_SOURCE = _csp_sha256(_SCRIPT_BODY)


def render_setup_form(
    *,
    team_id: str,
    token: str,
    team_name: str | None = None,
    error: str | None = None,
    values: Mapping[str, object] | None = None,
) -> str:
    """Render the BYOK credential form for a Slack workspace."""
    escaped_token = escape(token, quote=True)
    form_values = values or {}
    error_banner = (
        ""
        if error is None
        else f"""
      <div class="error-banner" role="alert">
        <strong>Configuration was not saved</strong>
        <span>{escape(error)}</span>
      </div>"""
    )
    region_value = escape(str(form_values.get("aws_region") or "us-east-1"), quote=True)
    bucket_value = escape(str(form_values.get("s3_bucket") or ""), quote=True)
    prefix_value = escape(str(form_values.get("s3_prefix") or ""), quote=True)
    return _page_shell(
        title="Nimbus Slack setup",
        body=f"""
  <main class="shell">
    <header class="page-head">
      <span class="brand-mark" aria-hidden="true"></span>
      <p class="eyebrow">Workspace configuration</p>
      <h1>Connect Nimbus to your tools</h1>
      <p class="lede">
        Nimbus encrypts your BYOK credentials at rest and uses them only for
        this workspace. Rotate them anytime by reinstalling the app.
      </p>
      {_workspace_tag_html(team_id, team_name)}
    </header>

{error_banner}
    <form
      class="panel"
      method="post"
      action="/slack/setup/{escaped_token}"
      autocomplete="on"
      novalidate
    >
      <div class="field">
        <div class="field__head">
          <label for="openrouter_api_key">OpenRouter API key</label>
          {_reveal_button("openrouter_api_key")}
        </div>
        <input
          id="openrouter_api_key"
          name="openrouter_api_key"
          type="password"
          autocomplete="section-openrouter current-password"
          autocapitalize="none"
          data-1p-label="OpenRouter API key"
          spellcheck="false"
          required
        >
        <p class="field__hint">
          Used by Nimbus to call the model on this workspace's behalf.
        </p>
      </div>

      <div class="field">
        <div class="field__head">
          <label for="aws_access_key_id">AWS access key ID</label>
          {_reveal_button("aws_access_key_id")}
        </div>
        <input
          id="aws_access_key_id"
          name="aws_access_key_id"
          type="password"
          autocomplete="section-aws username"
          autocapitalize="none"
          data-1p-label="AWS access key ID"
          spellcheck="false"
          required
        >
      </div>

      <div class="field">
        <div class="field__head">
          <label for="aws_secret_access_key">AWS secret access key</label>
          {_reveal_button("aws_secret_access_key")}
        </div>
        <input
          id="aws_secret_access_key"
          name="aws_secret_access_key"
          type="password"
          autocomplete="section-aws current-password"
          autocapitalize="none"
          data-1p-label="AWS secret access key"
          spellcheck="false"
          required
        >
      </div>

      <div class="field">
        <label for="aws_region">AWS region</label>
        <input
          id="aws_region"
          name="aws_region"
          value="{region_value}"
          autocomplete="section-aws address-level1"
          autocapitalize="none"
          spellcheck="false"
          required
        >
      </div>

      <div class="field">
        <label for="s3_bucket">S3 bucket</label>
        <input
          id="s3_bucket"
          name="s3_bucket"
          value="{bucket_value}"
          autocomplete="section-aws organization"
          autocapitalize="none"
          spellcheck="false"
          required
        >
      </div>

      <div class="field">
        <label for="s3_prefix">
          S3 prefix
          <span class="field__optional">optional</span>
        </label>
        <input
          id="s3_prefix"
          name="s3_prefix"
          value="{prefix_value}"
          placeholder="optional/prefix"
          autocomplete="off"
          autocapitalize="none"
          spellcheck="false"
        >
        <p class="field__hint">Leave empty to use the bucket root.</p>
      </div>

      <button type="submit" class="primary">Save configuration</button>
      <p class="fine-print">
        Secrets are submitted over HTTPS, encrypted before storage, and never
        accepted through Slack messages.
      </p>
    </form>

    <ul class="trust-list" aria-label="What this form does">
      <li><span class="dot"></span> Submitted over HTTPS</li>
      <li><span class="dot"></span> KMS envelope encryption when configured</li>
      <li><span class="dot"></span> Never accepted through Slack messages</li>
    </ul>
  </main>
{_REVEAL_SCRIPT}""",
    )


def render_install_success(*, team_id: str, setup_path: str) -> str:
    """Render the OAuth completion page that links into BYOK setup."""
    escaped_team_id = escape(team_id)
    escaped_setup_path = escape(setup_path, quote=True)
    return _page_shell(
        title="Nimbus Slack installed",
        body=f"""
  <main class="shell shell--wide">
    <section class="card centered" aria-labelledby="installed-title">
      <span class="brand-mark" aria-hidden="true"></span>
      <p class="eyebrow">Slack app installed</p>
      <h1 id="installed-title">Finish connecting Nimbus</h1>
      <p class="lede">
        Nimbus is installed for workspace <strong>{escaped_team_id}</strong>.
        Add your OpenRouter and AWS credentials so Nimbus can answer questions
        and save Slack files to your S3 bucket.
      </p>
      <a class="button-link" href="{escaped_setup_path}">Continue setup</a>
    </section>
  </main>""",
    )


def render_setup_error(*, title: str, message: str) -> str:
    """Render a human-readable setup error page instead of a JSON payload."""
    return _page_shell(
        title=title,
        body=f"""
  <main class="shell shell--wide">
    <section class="card centered" aria-labelledby="setup-error-title">
      <span class="brand-mark" aria-hidden="true"></span>
      <p class="eyebrow">Setup needs attention</p>
      <h1 id="setup-error-title">{escape(title)}</h1>
      <p class="lede">{escape(message)}</p>
    </section>
  </main>""",
    )


def render_setup_success(*, team_id: str, team_name: str | None = None) -> str:
    """Render the post-setup onboarding page.

    The page has four sections:
    1. Success confirmation with a return-to-Slack link.
    2. What Nimbus can do — brief capabilities overview.
    3. Four-step getting-started guide (invite → home tab → first prompt → CLI).
    4. Expandable "More ideas" prompt gallery.
    """
    escaped_team_id = escape(team_id)
    workspace_display = (
        f"{escape(team_name)} [{escaped_team_id}]" if team_name else escaped_team_id
    )
    slack_app_link = f"https://app.slack.com/client/{escape(team_id, quote=True)}"
    sample_prompt = secrets.choice(_ONBOARDING_PROMPTS)
    sample_prompt_html = escape(sample_prompt)
    prompt_list_items = "\n".join(
        f"          <li>{escape(prompt)}</li>" for prompt in _ONBOARDING_PROMPTS
    )
    return _page_shell(
        title="Nimbus Slack setup complete",
        body=f"""
  <main class="shell shell--wide">

    <section class="card centered" aria-labelledby="success-title">
      <span class="brand-mark" aria-hidden="true"></span>
      <p class="eyebrow">Configuration saved</p>
      <h1 id="success-title">Nimbus is ready in Slack</h1>
      <p class="lede">
        Workspace <strong>{workspace_display}</strong> is connected. Close this
        tab and head back to Slack — Nimbus replies when you mention it.
      </p>
      <a class="button-link" href="{slack_app_link}">Return to Slack</a>
    </section>

    <section class="card" aria-labelledby="caps-title">
      <p class="eyebrow">What Nimbus does</p>
      <h2 id="caps-title">Three things you can do right now</h2>
      <ul class="prompt-list" style="margin-top:.5rem">
        <li>
          <strong>Back up Slack files to S3</strong> —
          save, diff, and deduplicate files across channels without leaving Slack.
        </li>
        <li>
          <strong>Answer questions about your data</strong> —
          summarise PDFs, spot anomalies, find sensitive files, explain
          differences between versions.
        </li>
        <li>
          <strong>Monitor workspace health</strong> —
          track running tasks, pending approvals, and cloud costs in the
          Nimbus App Home tab or with <code>@Nimbus status</code>.
        </li>
      </ul>
    </section>

    <section class="card" aria-labelledby="start-title">
      <p class="eyebrow">Get started</p>
      <h2 id="start-title">Four steps to your first reply</h2>
      <ol class="steps">
        <li>
          <span class="step-num" aria-hidden="true">1</span>
          <div>
            <h3>Invite Nimbus to a channel</h3>
            <p>From any channel where you want file backup or AI answers, run:</p>
            <code class="prompt">/invite @Nimbus</code>
          </div>
        </li>
        <li>
          <span class="step-num" aria-hidden="true">2</span>
          <div>
            <h3>Open the Nimbus App Home tab</h3>
            <p>
              Click <strong>Nimbus</strong> in Slack&#8217;s sidebar, then
              choose the <em>Home</em> tab to see a live dashboard of running
              tasks, pending approvals, and workspace health.
            </p>
          </div>
        </li>
        <li>
          <span class="step-num" aria-hidden="true">3</span>
          <div>
            <h3>Try your first prompt</h3>
            <p>This one always works — it exercises the file pipeline directly:</p>
            <code class="prompt">{escape(_FIRST_PROMPT)}</code>
            <p class="hint">Or try this — refresh for a different idea:</p>
            <code class="prompt">@Nimbus {sample_prompt_html}</code>
          </div>
        </li>
        <li>
          <span class="step-num" aria-hidden="true">4</span>
          <div>
            <h3>
              Install the Nimbus CLI
              <span class="field__optional">Optional</span>
            </h3>
            <p>
              Manage tasks, plans, artifacts, and approvals from your
              terminal:
            </p>
            <code class="prompt">pip install nimbus-cli</code>
            <p class="hint">
              Then try: <code>nimbus status</code> or
              <code>nimbus task list</code>
            </p>
          </div>
        </li>
      </ol>

      <details class="more">
        <summary>More ideas</summary>
        <ul class="prompt-list">
{prompt_list_items}
        </ul>
      </details>
    </section>

  </main>""",
    )


_INLINE_STYLE_BODY = r"""
    :root {
      color-scheme: dark;
      --bg: #09090b;
      --bg-2: #09090b;
      --surface: #18181b;
      --surface-2: #27272a;
      --ink: #fafafa;
      --ink-2: #e4e4e7;
      --muted: #a1a1aa;
      --muted-2: #71717a;
      --line: #27272a;
      --line-strong: #3f3f46;
      --accent: #10a37f;
      --accent-hover: #0e8f70;
      --accent-ink: #d6fff2;
      --danger: #f87171;
      --danger-bg: rgba(248, 113, 113, .08);
      --ring: #10a37f;
      --code-bg: #18181b;
      --code-ink: #e4e4e7;
      --radius-card: 8px;
      --radius-control: 6px;
      --radius-pill: 999px;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      min-height: 100vh;
      padding: 56px 20px 72px;
      background: var(--bg);
      color: var(--ink);
      font-family:
        "Söhne", "Inter", ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      font-feature-settings: "cv02", "cv03", "cv04", "cv11", "ss01";
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      line-height: 1.55;
      font-size: 15px;
    }
    @media (max-width: 600px) {
      body { padding: 32px 16px 56px; font-size: 14px; }
    }

    /* ── Layout ────────────────────────────────────────────────────── */
    .shell {
      width: 100%;
      max-width: 520px;
      margin: 0 auto;
      display: grid;
      gap: 24px;
    }
    .shell--wide { max-width: 720px; gap: 20px; }

    /* ── Typography ────────────────────────────────────────────────── */
    h1 {
      margin: 0;
      font-size: clamp(1.6rem, 2.4vw + .8rem, 2.1rem);
      font-weight: 600;
      letter-spacing: 0;
      line-height: 1.15;
      color: var(--ink);
    }
    h2 {
      margin: 0 0 4px;
      font-size: 1rem;
      font-weight: 600;
      letter-spacing: 0;
      color: var(--ink);
    }
    h3 {
      margin: 0 0 2px;
      font-size: .92rem;
      font-weight: 600;
      letter-spacing: 0;
      color: var(--ink);
    }
    p { margin: 0; }
    .eyebrow {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .lede {
      margin-top: 8px;
      max-width: 60ch;
      color: var(--muted);
      font-size: .96rem;
      line-height: 1.55;
    }
    .fine-print {
      margin-top: 14px;
      color: var(--muted);
      font-size: .78rem;
      line-height: 1.55;
    }

    /* ── Header / brand ────────────────────────────────────────────── */
    .page-head {
      display: grid;
      gap: 10px;
      padding: 4px;
    }
    .brand-mark {
      display: inline-block;
      width: 22px;
      height: 22px;
      border-radius: 6px;
      background: var(--accent);
      margin-bottom: 6px;
    }

    /* ── Workspace tag ─────────────────────────────────────────────── */
    .workspace-tag {
      margin-top: 6px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 4px 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius-pill);
      background: var(--surface);
      font-size: .78rem;
      font-feature-settings: "tnum";
      width: max-content;
    }
    .workspace-tag__label { color: var(--muted); font-weight: 500; }
    .workspace-tag__value {
      font-weight: 600;
      color: var(--ink-2);
      font-family: "Söhne Mono", "JetBrains Mono", ui-monospace, monospace;
      font-size: .76rem;
    }
    .workspace-tag__id {
      color: var(--muted-2);
      font-family: "Söhne Mono", "JetBrains Mono", ui-monospace, monospace;
      font-size: .72rem;
    }

    /* ── Trust list ────────────────────────────────────────────────── */
    .trust-list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 6px 18px;
    }
    .trust-list li {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: .82rem;
    }
    .dot {
      width: 5px; height: 5px;
      border-radius: var(--radius-pill);
      background: var(--accent);
    }

    /* ── Cards / panels ────────────────────────────────────────────── */
    .panel, .card {
      border: 1px solid var(--line);
      border-radius: var(--radius-card);
      background: var(--surface);
      padding: clamp(20px, 3vw, 28px);
    }
    .card.centered { text-align: center; }
    .card.centered .brand-mark { display: block; margin: 0 auto 10px; }
    .card.centered .eyebrow { margin-bottom: 6px; }
    .card.centered .lede {
      margin-left: auto;
      margin-right: auto;
    }
    .card.centered .button-link {
      display: inline-flex;
      margin: 18px auto 0;
    }

    /* ── Form fields ───────────────────────────────────────────────── */
    .field { margin-bottom: 14px; }
    .field:last-of-type { margin-bottom: 18px; }
    .field__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .field__hint {
      margin-top: 6px;
      color: var(--muted);
      font-size: .78rem;
    }
    .field__optional {
      margin-left: 6px;
      color: var(--muted-2);
      font-size: .66rem;
      font-weight: 500;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    label {
      display: block;
      margin-bottom: 6px;
      font-size: .82rem;
      font-weight: 500;
      letter-spacing: 0;
      color: var(--ink-2);
    }
    input {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line-strong);
      border-radius: var(--radius-control);
      padding: 8px 12px;
      color: var(--ink);
      background: var(--bg-2);
      font: inherit;
      font-size: .92rem;
      transition: border-color .15s, box-shadow .15s, background .15s;
    }
    input::placeholder { color: var(--muted-2); }
    input:hover { border-color: var(--line-strong); }
    input:focus {
      outline: none;
      border-color: var(--ring);
      box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--ring);
    }
    input:-webkit-autofill,
    input:-webkit-autofill:focus {
      -webkit-text-fill-color: var(--ink);
      -webkit-box-shadow: 0 0 0 1000px var(--bg-2) inset;
      caret-color: var(--ink);
    }

    /* ── Buttons ───────────────────────────────────────────────────── */
    button.primary, .button-link {
      display: inline-flex;
      justify-content: center;
      align-items: center;
      width: 100%;
      min-height: 40px;
      padding: 0 16px;
      border: 0;
      border-radius: var(--radius-control);
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 500;
      font-size: .92rem;
      letter-spacing: 0;
      text-decoration: none;
      cursor: pointer;
      transition: background .15s;
    }
    button.primary:hover, .button-link:hover { background: var(--accent-hover); }
    button.primary:active, .button-link:active { background: #0b765d; }
    button.primary:focus-visible, .button-link:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .button-link {
      width: auto;
      padding: 0 18px;
    }

    /* ── Reveal control ────────────────────────────────────────────── */
    .reveal {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      padding: 0;
      transition: color .15s, background .15s;
    }
    .reveal:hover {
      color: var(--ink);
      background: var(--surface-2);
    }
    .reveal:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .reveal svg .reveal-slash {
      opacity: 0;
      transition: opacity .15s;
    }
    .reveal[aria-pressed="true"] svg .reveal-slash { opacity: 1; }

    .error-banner {
      display: grid;
      gap: 4px;
      border: 1px solid rgba(248, 113, 113, .35);
      border-radius: var(--radius-card);
      background: var(--danger-bg);
      color: var(--ink);
      padding: 12px 14px;
    }
    .error-banner strong {
      color: var(--danger);
      font-size: .86rem;
      font-weight: 600;
    }
    .error-banner span {
      color: var(--ink-2);
      font-size: .84rem;
      line-height: 1.45;
    }

    /* ── Numbered steps ────────────────────────────────────────────── */
    .steps {
      list-style: none;
      margin: 6px 0 0;
      padding: 0;
      display: grid;
      gap: 18px;
    }
    .steps > li {
      display: grid;
      grid-template-columns: 24px 1fr;
      gap: 14px;
      align-items: start;
    }
    .step-num {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px; height: 22px;
      margin-top: 1px;
      border: 1px solid var(--line-strong);
      border-radius: var(--radius-pill);
      background: var(--bg-2);
      color: var(--ink-2);
      font-weight: 600;
      font-size: .72rem;
      font-feature-settings: "tnum";
    }
    .steps p {
      margin: 4px 0 6px;
      color: var(--muted);
      font-size: .88rem;
      line-height: 1.5;
    }
    .steps p.hint { margin-top: 10px; }

    /* ── Inline code / prompt blocks ───────────────────────────────── */
    code {
      font-family:
        "Söhne Mono", "JetBrains Mono", ui-monospace, "SF Mono", Menlo,
        Consolas, monospace;
      font-size: .82rem;
      color: var(--code-ink);
    }
    .prompt {
      display: block;
      margin-top: 6px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius-control);
      background: var(--code-bg);
      color: var(--code-ink);
      overflow-x: auto;
      white-space: nowrap;
      letter-spacing: 0;
    }

    /* ── Details / more ────────────────────────────────────────────── */
    .more { margin-top: 18px; }
    .more summary {
      cursor: pointer;
      color: var(--ink-2);
      font-weight: 500;
      font-size: .85rem;
      list-style: none;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .more summary:hover { color: var(--ink); }
    .more summary::-webkit-details-marker { display: none; }
    .more summary::before {
      content: "\203A";
      display: inline-block;
      transition: transform .15s;
      color: var(--muted);
    }
    .more[open] summary::before { transform: rotate(90deg); }

    /* ── Prompt list (idea gallery + capabilities cards) ───────────── */
    .prompt-list {
      margin: 12px 0 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 8px;
    }
    .prompt-list li {
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius-control);
      background: var(--bg-2);
      color: var(--ink-2);
      font-size: .85rem;
      line-height: 1.5;
    }
    .prompt-list li strong { color: var(--ink); font-weight: 600; }

    /* ── Wide-shell two-column at >=860px ──────────────────────────── */
    @media (min-width: 860px) {
      .shell--wide { gap: 24px; }
    }
  """

_INLINE_STYLE_CSP_SOURCE = _csp_sha256(_INLINE_STYLE_BODY)


def csp_header_value() -> str:
    """Return the Content-Security-Policy header for setup pages."""
    return (
        "default-src 'none'; "
        f"style-src {_INLINE_STYLE_CSP_SOURCE}; "
        f"script-src {_INLINE_SCRIPT_CSP_SOURCE}; "
        "img-src 'self' data:; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )


def _page_shell(*, title: str, body: str) -> str:
    """Render the shared Nimbus setup page shell."""
    escaped_title = escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>{_INLINE_STYLE_BODY}</style>
</head>
<body>
{body}
</body>
</html>"""


def _required_secret(payload: Mapping[str, object], key: str) -> str:
    """Return a non-empty secret string without control characters."""
    value = _required_text(payload, key)
    if any(char in value for char in "\r\n\t"):
        msg = f"{key} must not contain control characters."
        raise TenantSetupError(msg)
    return value


def _required_region(payload: Mapping[str, object], key: str) -> str:
    """Return a syntactically valid AWS region."""
    value = _required_text(payload, key)
    if not _AWS_REGION_RE.fullmatch(value):
        msg = "aws_region must look like us-east-1."
        raise TenantSetupError(msg)
    return value


def _required_bucket(payload: Mapping[str, object], key: str) -> str:
    """Return a syntactically valid S3 bucket name."""
    value = _required_text(payload, key)
    if (
        not _S3_BUCKET_RE.fullmatch(value)
        or ".." in value
        or ".-" in value
        or "-." in value
    ):
        msg = "s3_bucket must be a valid S3 bucket name."
        raise TenantSetupError(msg)
    return value


def _required_text(payload: Mapping[str, object], key: str) -> str:
    """Return a required non-empty string."""
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    msg = f"{key} must be a non-empty string."
    raise TenantSetupError(msg)


def _optional_text(payload: Mapping[str, object], key: str) -> str:
    """Return an optional string value."""
    value = payload.get(key, "")
    if isinstance(value, str):
        return value.strip()
    msg = f"{key} must be a string."
    raise TenantSetupError(msg)
