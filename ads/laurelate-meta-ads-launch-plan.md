# Laurelate — Meta Ads Launch Plan

_Prepared 6 July 2026. Goal: sales / purchases._

## Where things stand today (pulled live from the account)

- **Connector is live.** Claude can read and (with your approval on every write) act on the Laurelate ad account (`2279313035795134`, USD, active, payment method on file).
- **Structure score: 100/100.** Meta's Opportunity Score has zero outstanding recommendations. The account already follows best practices.
- **Purchase tracking works.** Your pixel (`pixel1`) is active and fired as recently as today, so Meta can attribute purchases. This is the one thing that has to be right for a sales goal, and it is.
- **One campaign exists but has never run.** "Advantage+ shopping campaign 10/22/2024" is set to Active, but its delivery status is *inactive — no ads*. The campaign and ad set are empty shells; no ad was ever added, so there's been zero spend and zero data.

**What this means:** there is nothing to *optimize* yet. Optimization is a post-launch activity — it needs a week of real delivery data. The actual first move is to **launch your first ad.**

## Two decisions still open

1. **Product + destination.** What is the first thing we're selling, and what URL should the ad send people to (product page / site / checkout)? A sales campaign needs a buy destination.
2. **Creative source.** Two routes, to decide together:
   - **Fresh:** you give me the product + link, I write hook / body / caption variants across three angles (pain-first, outcome-first, social-proof-first); you supply the image or video.
   - **Adapted:** you share an existing Instagram post that already performed; I rework it into ad copy and we rebuild it as an ad. _(Note: the API can't auto-pull your IG posts for this account yet, so you'd share the post link/screenshot.)_

## The launch sequence

**Step 1 — Lock the offer and angles (in Claude, before Ads Manager).**
Decide the product, price, destination URL, and ideal customer. I generate 3 distinct angles so we have variety before building anything.

**Step 2 — Use the existing campaign.** It's already the right type (Advantage+ Sales) and scores 100. We add an ad set budget and drop the first ads into it rather than building a new campaign from scratch.

**Step 3 — Build 5+ creative variants, not 2.** Advantage+ tests combinations automatically, and creative diversity is the biggest lever once targeting is on autopilot. Format for 9:16 (vertical) first — that's ~90% of Meta placement inventory now.

**Step 4 — Set budget for the learning phase.** For a purchase campaign, an ad set needs roughly ~10 purchase events/week to exit "learning" and stabilize. Budget should be set with that in mind relative to the product price — we'll size it once we know the price point.

**Step 5 — Publish and leave it alone for 7 days.** Every meaningful edit resets the learning phase. This is the hard part. No tweaking during the first week.

**Step 6 — Then optimize (this is where Claude earns its keep).** After 7 days I diagnose, not just report:
   - Re-check Opportunity Score and flag which recommendations to skip (the ones that fight a tight, intentional setup).
   - Find ad sets with CPA >20% over target and tell you whether it's *creative fatigue* or *targeting*.
   - Draft fresh variants for fatigued ads, keeping the winning hook.

## Prompts to use after launch

- "Pull the opportunity score and explain the top 3 recommendations in plain language; tell me which conflict with our setup."
- "Show ad sets with CPA more than 20% above target this week and tell me if it's creative fatigue or targeting."
- "Draft 5 new creative variants for the highest-frequency ad set, keeping last week's winning hook."

## What Claude does vs. what stays yours

Meta's AI runs delivery, targeting, and auction. Claude is the analyst and copywriter on top: judgment on Meta's own advice, brand-voice creative on demand, fast diagnosis. What stays yours: whether the offer itself is good, and brand taste. Neither Meta nor Claude has taste.

## Immediate next step

Tell me **what Laurelate sells and the destination URL**, and we'll make the fresh-vs-adapted creative call together. That unblocks Step 1.
