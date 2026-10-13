"""Playwright session with a compact text snapshot and short element ids (e1, e2, ...)."""
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from agent import state as S

SNAPSHOT_JS = """
() => {
  const sel = 'a[href], button, input, select, textarea, [role=button]';
  document.querySelectorAll('[data-aid]').forEach(e => e.removeAttribute('data-aid'));
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || (r.width === 0 && r.height === 0)) continue;
    if (el.type === 'hidden') continue;
    n++; const id = 'e' + n; el.setAttribute('data-aid', id);
    const tag = el.tagName.toLowerCase();
    let label = (el.labels && el.labels.length) ? el.labels[0].innerText.trim() : '';
    label = label || el.getAttribute('aria-label') || el.placeholder || '';
    const text = (tag === 'input' ? '' : (el.innerText || '')).trim().replace(/\\s+/g, ' ').slice(0, 60);
    let line = `[${id}] ${tag}` + (tag === 'input' ? `(${el.type})` : '');
    if (text) line += ` "${text}"`;
    if (label) line += ` label="${label}"`;
    if (el.name) line += ` name=${el.name}`;
    if (tag === 'a') line += ` href=${el.getAttribute('href')}`;
    if (tag === 'select') line += ` options=[${[...el.options].map(o => o.text).join(' | ')}] value="${el.value}"`;
    else if ((tag === 'input' || tag === 'textarea') && el.type !== 'submit') line += ` value="${el.value}"`;
    if (el.dataset.role === 'submit-record') line += ' [GATED: use submit_record tool]';
    out.push(line);
  }
  return out;
}
"""


class Browser:
    def __init__(self):
        self._pw = self.browser = self.context = self.page = None

    async def start(self, headless: bool = True):
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=headless)
        self.context = await self.browser.new_context(viewport={"width": 1100, "height": 800})
        self.page = await self.context.new_page()

    async def close(self):
        for obj in (self.context, self.browser):
            try:
                if obj:
                    await obj.close()
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()

    async def _settle(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    @staticmethod
    def _path(url: str) -> str:
        return urlparse(url).path.rstrip("/")

    async def _authenticated(self) -> bool:
        """Probe a protected page with the session cookie (no redirects, the page itself is not moved)."""
        r = await self.context.request.get(f"{S.SANDBOX_URL}/vendors", max_redirects=0)
        return r.status == 200

    async def ensure_logged_in(self) -> None:
        """Authenticate in code (never by the LLM). Opens /login only if the session is not authenticated."""
        if await self._authenticated():
            return
        await self.page.goto(f"{S.SANDBOX_URL}/login", wait_until="domcontentloaded")
        await self.page.locator('input[name="username"]').fill(S.ERP_USERNAME)
        await self.page.locator('input[name="password"]').fill(S.ERP_PASSWORD)
        await self.page.locator('button[type="submit"]').click()
        await self.page.wait_for_url(lambda u: self._path(u) != "/login", timeout=5000)
        await self._settle()

    async def goto(self, url: str) -> str:
        resp = await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()
        if self._path(self.page.url) == "/login" and self._path(url) != "/login":
            await self.ensure_logged_in()               # session expired: re-authenticate, repeat once
            resp = await self.page.goto(url, wait_until="domcontentloaded")
            await self._settle()
        status = resp.status if resp else "?"
        return f"HTTP {status}"

    async def snapshot(self, limit: int = 60) -> str:
        lines = await self.page.evaluate(SNAPSHOT_JS)
        head = f"URL: {self.page.url}\nTitle: {await self.page.title()}"
        extra = f"\n... {len(lines) - limit} more elements" if len(lines) > limit else ""
        return head + "\n" + "\n".join(lines[:limit]) + extra

    def _loc(self, element_id: str):
        if not re.fullmatch(r"e\d+", element_id):
            raise ValueError(f"bad element id {element_id!r}; ids look like e3")
        return self.page.locator(f'[data-aid="{element_id}"]')

    async def click(self, element_id: str) -> str:
        loc = self._loc(element_id)
        if await loc.count() == 0:
            return f"ERROR: unknown element {element_id}; call snapshot() again"
        if await loc.first.get_attribute("data-role") == "submit-record":
            return "BLOCKED: this button is gated by policy. Use the submit_record tool instead."
        await loc.first.click()
        await self._settle()
        return f"Clicked {element_id}."

    async def fill(self, element_id: str, value: str) -> str:
        loc = self._loc(element_id)
        if await loc.count() == 0:
            return f"ERROR: unknown element {element_id}; call snapshot() again"
        tag = await loc.first.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            try:
                await loc.first.select_option(label=value)
            except Exception:
                await loc.first.select_option(value=value)
        else:
            await loc.first.fill(value)
        return f"Filled {element_id} with {value!r}."

    async def read_text(self, limit: int = 3000) -> str:
        text = await self.page.inner_text("body")
        text = re.sub(r"\n{2,}", "\n", text).strip()
        return f"URL: {self.page.url}\n" + (text[:limit] + ("..." if len(text) > limit else ""))

    async def screenshot(self, path: Path) -> str:
        await self.page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def press_gated_submit(self) -> None:
        """Used only by submit_record after the policy gate passed."""
        loc = self.page.locator('[data-role="submit-record"]')
        if await loc.count() == 0:
            raise RuntimeError("No submit button on this page")
        await loc.first.click()
        await self._settle()
