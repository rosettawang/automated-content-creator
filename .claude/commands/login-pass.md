---
description: Work the account chores that need a logged-in human, following the Login-gated queue in specs/index.html
---

Run the login pass: the standing queue of work Claude cannot do at all — a
password, an OAuth consent screen, accepting terms, a secret's value, a 2FA code,
or the owner's confirming click on an irreversible publish/post/authorize.

**The queue is not in this file.** Read the **Login-gated queue** section of
`specs/index.html` and work it in order. That section is the single source of
truth for what's outstanding; this command exists only so the whole sitting runs
from one line. If this file and the index ever disagree, the index wins.

**"Needs a browser" is not the test — "needs the owner" is.** The Chrome
extension drives a logged-in session, so a dashboard toggle, filling a
non-credential form field, or opening an OAuth flow up to the consent screen is
ordinary work, not a queue item. If something in the queue turns out to be doable
without the owner, do it and say so rather than leaving it staged.

Arguments (optional): `$ARGUMENTS` — narrow the pass to one service or item, e.g.
`Instagram only`. With no arguments, work the whole queue.

How to work it:

1. **Open each item in its own Chrome tab**, in the order the queue lists them,
   and take each as far as it can go without the owner — navigating, filling
   non-credential fields, staging the action.
2. **Batch it.** The point is one sitting instead of four dashboards four times.
   Don't stop between items to report; stage everything, then report once.
3. **Stop at the final click on anything irreversible or authenticating.** Leave
   the tab open, focused on the exact button, and say so. For this app the
   canonical one is the first real Instagram post: Claude drives the go-live
   steps and reports the dry-run payload, but **never** arms the campaign, sets
   `SOCIAL_DRY_RUN=0`, or presses publish.
4. **Never do these, even if asked:** enter a password, create an account, accept
   terms on the owner's behalf, grant an OAuth permission, or handle a secret's
   value. Those are the owner's by design. Everything up to that click is not.
5. **Verify rather than assume.** A greyed-out button or a dashboard that looks
   right is not evidence. Where a real check can prove it — the connection
   listing ACTIVE, the post visible, an import completing — check that instead.
6. **Update the queue as you go.** Strike finished rows with the date, add
   anything newly discovered to be login-gated, and clear the owning spec's
   `spec-account` flag once its gate is done. Then say what is genuinely left and
   who owns it.

Report at the end in a few lines: what is done and verified, what is staged and
waiting on one click, and what turned out to need something the owner doesn't
have yet.
