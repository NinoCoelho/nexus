# Plan: Cancel/Reactivate Subscription Buttons

## Changes needed

### 1. `lib/stripe.ts` — Add helpers

```ts
export async function cancelSubscription(subscriptionId: string) {
  return getStripe().subscriptions.update(subscriptionId, {
    cancel_at_period_end: true,
  });
}

export async function reactivateSubscription(subscriptionId: string) {
  return getStripe().subscriptions.update(subscriptionId, {
    cancel_at_period_end: false,
  });
}
```

### 2. New routes

**`src/app/api/billing/cancel/route.ts`:**
- Verify Firebase idToken
- Look up user → `stripeSubscriptionId`
- Call `cancelSubscription(subId)`
- Compute `cancelsAt` from `current_period_end`
- `upsertUser(uid, { cancelsAt })`
- Return `{ cancelsAt }`

**`src/app/api/billing/reactivate/route.ts`:**
- Verify Firebase idToken
- Look up user → `stripeSubscriptionId`
- Call `reactivateSubscription(subId)`
- `upsertUser(uid, { cancelsAt: null })`
- Return `{ cancelsAt: null }`

### 3. `lib/api-client.ts` — Add methods

```ts
cancel: (idToken: string) =>
  apiFetch<{ cancelsAt: string }>("/billing/cancel", {
    method: "POST",
    body: JSON.stringify({ idToken }),
  }),
reactivate: (idToken: string) =>
  apiFetch<{ cancelsAt: null }>("/billing/reactivate", {
    method: "POST",
    body: JSON.stringify({ idToken }),
  }),
```

### 4. `app/billing/page.tsx` — Update pro view

Remove the redirect for pro users (line 16: `if (!loading && user && tier === "pro") router.push("/account")`).

Replace the pro billing section with:
- Pro + active (`!cancelsAt`): plan details + "Cancel subscription" button
- Pro + cancelling (`cancelsAt`): plan details + "Cancels Jun 3" notice + "Reactivate" button
- Both: secondary "Manage in Stripe" link (existing portal flow)

Cancel/reactivate handlers:
- Call `api.billing.cancel(idToken)` / `api.billing.reactivate(idToken)`
- Update `cancelsAt` state
- Call `refreshStatus()` to sync auth context

### 5. `lib/auth-context.tsx` — Expose `cancelsAt` updates

Add a `setCancelsAtFromBilling` helper (or just use `setCancelsAt` directly since it's already in state). The billing page can call `refreshStatus()` after cancel/reactivate to get the updated `cancelsAt` from verify.

### 6. `NexusTab.tsx` — Already correct

The "Manage subscription" button already opens `/billing` in a popup and refreshes on close. No changes needed.

## Execution order

1. Add stripe helpers
2. Create cancel + reactivate routes
3. Update api-client
4. Update billing page (remove redirect, add buttons)
5. Build, deploy to Vercel, push
