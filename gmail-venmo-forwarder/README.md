# Gmail Venmo "paid you" auto-forwarder

> **Need forwards within seconds?** Use the hosted IMAP IDLE worker in [`server/`](server/README.md). The Apps Script below checks once a minute.

A Google Apps Script that forwards every Gmail message containing **both**
"paid you" and "Venmo" to 4 recipients.

## Setup (about 3 minutes)

1. Go to <https://script.google.com> while signed in to the Gmail account
   that receives the Venmo emails, and click **New project**.
2. Delete the placeholder code and paste in the contents of [`Code.gs`](Code.gs).
3. Replace the 4 addresses in `RECIPIENTS` at the top of the file with the
   real recipients, then save (Ctrl/Cmd+S).
4. In the function dropdown pick **`setup`** and click **Run**. Approve the
   permissions prompt (choose *Advanced → Go to project* if Google shows an
   "unverified app" warning; this is your own script).

That's it. The script now checks every minute, forwards new matches, and
tags each handled thread with the Gmail label **Venmo-Forwarded** so nothing
is sent twice.

To stop it, run **`teardown`**.

## How it works

- A time-driven trigger runs `forwardVenmoPayments` every
  `CHECK_EVERY_MINUTES` minutes.
- It searches Gmail for `"paid you" venmo` in mail from the last `LOOKBACK`
  days that doesn't already have the label, then double-checks each message's
  subject and body contain both terms before forwarding.
- Spam, trash and your own sent mail are skipped.

## Notes

- **Daily limits:** consumer Gmail accounts can send to about 100 recipients a
  day from Apps Script (Workspace accounts: 1,500). With 4 recipients that's
  roughly 25 forwarded Venmo emails per day on a free account.
- **Why not a Gmail filter?** Gmail filters can forward to only one address,
  and each address must be verified in Settings first. This script avoids both.
- Forwarded emails come from your own address with the original message
  quoted, just like clicking **Forward** by hand.
