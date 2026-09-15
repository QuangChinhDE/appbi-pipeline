import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

/**
 * The four states the first screen of the morning has to get right.
 *
 * These assert what the page *says*, not how it is painted: the selectors are
 * roles and text, so a restyle does not break them and a broken flow cannot
 * pass. Nothing here takes a screenshot -- a screenshot test goes green while
 * the incident card renders three times, which is exactly the failure this
 * page was rebuilt to prevent.
 *
 * The API is served from payloads generated out of `OverviewResponse`; see
 * `backend/tests/test_overview_e2e_fixtures.py`, which fails if they drift.
 */

const WS = 'c25eb1b9-3b5c-4173-a877-ff639880a535';
const SOURCE = '203a2d53-58d0-4189-8cca-9545de39f3cd';

const fixture = (name: string) =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', `${name}.json`), 'utf8'));

/** Signed in, in one workspace, with the Overview answering from `scenario`. */
async function openOverview(page: Page, scenario: string) {
  const me = {
    id: 'user-1', email: 'analyst@appbi.local', full_name: 'Analyst',
    locale: 'en', is_platform_admin: false,
    workspace: { id: WS, name: 'AppBI Data Team', slug: 'default' },
    workspaces: [{ id: WS, name: 'AppBI Data Team', slug: 'default', role: 'DATA_ADMIN' }],
    role: 'DATA_ADMIN',
    permissions: {}, levels: {},
    organization: { id: 'org-1', name: 'Default organization' },
    organization_permissions: {}, password_change_required: false,
    engine_capabilities: {},
  };

  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, json: body });

    if (path.endsWith('/auth/me')) return json(me);
    if (path.endsWith('/auth/config')) {
      return json({ password: true, google: false, google_client_id: '',
                    google_domains: [], default_locale: 'en' });
    }
    if (path.endsWith('/overview')) return json(fixture(scenario));
    if (path.endsWith('/alerts/unread-count')) return json({ unread: 0 });
    if (path.endsWith('/engine/status')) {
      return json({ label: 'ok', operational: true, active_runs: 0, queued_runs: 0 });
    }
    // Anything else this shell asks for is not what these flows are about.
    return json({ items: [], page: { total: 0 }, summary: {} });
  });

  await page.goto(`/workspaces/${WS}/overview`);
  await expect(page.getByRole('heading', { name: 'Data health' })).toBeVisible();
}

/** The issue cards, addressed the way the banner's own CTA addresses them. */
const issueCards = (page: Page) =>
  page.locator('#issues').getByRole('heading').or(page.locator('#issues'));

test.describe('Overview — golden flows', () => {
  test('an empty workspace is offered the four steps, not a clean bill of health',
    async ({ page }) => {
      await openOverview(page, 'empty-workspace');

      // The onboarding checklist takes the page.
      await expect(page.getByText('Get started in 4 steps')).toBeVisible();
      await expect(page.getByRole('link', { name: /Connect a source/i }).first())
        .toBeVisible();

      // And crucially, it does not claim the workspace is healthy.
      await expect(page.getByText('Everything looks fine')).toHaveCount(0);
      await expect(page.getByText('Needs your attention')).toHaveCount(0);
    });

  test('a healthy morning says so, and still shows the evidence',
    async ({ page }) => {
      await openOverview(page, 'healthy-morning');

      await expect(page.getByText('Everything looks fine')).toBeVisible();
      await expect(page.getByText('Nothing needs you right now.')).toBeVisible();
      await expect(page.getByText('No problems found.')).toBeVisible();

      // The sections that make the claim checkable are all present.
      await expect(page.getByText('Is the data on time')).toBeVisible();
      await expect(page.getByText('Reliability, 7 days')).toBeVisible();
      await expect(page.getByText('Data journey')).toBeVisible();
      await expect(page.getByText('Transform health')).toBeVisible();
      await expect(page.getByText('Platform')).toBeVisible();

      // Reliability reads as a rate with its direction, not a bare number.
      await expect(page.getByText('99.2%')).toBeVisible();
      await expect(page.getByText(/vs before/)).toBeVisible();
    });

  test('one dead credential is one card, naming the cause, the cost and the fix',
    async ({ page }) => {
      await openOverview(page, 'root-cause-incident');

      await expect(page.getByText('Needs attention now')).toBeVisible();

      // ONE incident. Three failed pipelines must not become three cards --
      // this is the assertion the whole page exists for.
      const remediation = page.getByRole('link', { name: /Update credentials/i });
      await expect(remediation).toHaveCount(1);

      // What happened, why, and what it costs.
      await expect(page.getByText('Legacy ERP can no longer sign in')).toBeVisible();
      await expect(
        page.getByText('The credentials for Legacy ERP are no longer valid.')).toBeVisible();
      await expect(page.getByText(/3 pipelines affected/)).toBeVisible();
      await expect(page.getByText(/2h 3m behind schedule/)).toBeVisible();

      // Every affected pipeline is named on that one card.
      for (const name of ['ERP purchase orders to warehouse',
                          'ERP stock movements to warehouse',
                          'ERP suppliers to warehouse']) {
        await expect(page.locator('#issues').getByText(name)).toBeVisible();
      }

      // And the CTA reaches the surface where the credential is actually fixed.
      await expect(remediation).toHaveAttribute(
        'href', new RegExp(`/workspaces/${WS}/sources/${SOURCE}`));
    });

  test('a long list of incidents folds instead of becoming a scroll',
    async ({ page }) => {
      await openOverview(page, 'many-incidents');

      // Six incidents, four shown. The most severe come first, because the
      // server sorts them, so the fold never hides something more urgent than
      // what it leaves visible.
      const cards = page.locator('#issues').getByText(/Pipeline \d is failing/);
      await expect(cards).toHaveCount(4);

      // The fold says how many are behind it rather than just "more".
      const more = page.getByRole('button', { name: 'Show 2 more' });
      await expect(more).toBeVisible();

      await more.click();
      await expect(cards).toHaveCount(6);
      await expect(page.getByRole('button', { name: 'Show less' })).toBeVisible();

      // And the trend sections are still reachable rather than pushed off by
      // the list, which is the reason the fold exists.
      await expect(page.getByText('Reliability, 7 days')).toBeVisible();
    });

  test('a bad number is never painted as good, and a stalled worker is visible',
    async ({ page }) => {
      await openOverview(page, 'degraded-platform');

      // 0% on time is a reading, not an absence. It used to render green
      // because zero is falsy. The label is uppercased by CSS, so match the
      // text the DOM actually holds rather than what the eye sees.
      const onTimeTile = page.getByText(/^On time$/i).locator('xpath=..');
      const value = onTimeTile.getByText('0%', { exact: true });
      await expect(value).toBeVisible();
      await expect(value).not.toHaveClass(/text-success/);

      // The machinery underneath is reported, and the worker that is gone
      // reads differently from the queues that are merely deep.
      await expect(page.getByText('Transform worker')).toBeVisible();
      await expect(page.getByText('Down')).toBeVisible();
      await expect(page.getByText('Scheduler')).toBeVisible();
      await expect(page.getByText('Engine operations')).toBeVisible();
      await expect(page.getByText('Backlog').first()).toBeVisible();

      // A Transform that cannot build its tables is stated, not implied.
      await expect(page.getByText('1 failing')).toBeVisible();
    });
});
