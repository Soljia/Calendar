/**
 * Gmail Venmo "paid you" auto-forwarder (Google Apps Script).
 *
 * Forwards every email that contains BOTH the phrase "paid you" AND the word
 * "Venmo" to a fixed list of recipients, then labels it so it is never
 * forwarded twice.
 *
 * Setup: see README.md in this folder. In short: paste this file into a new
 * project at https://script.google.com, edit RECIPIENTS, run `setup` once and
 * approve the permissions prompt.
 */

// ---- Configuration ---------------------------------------------------------

/** The 4 people who should receive the forwarded emails. */
const RECIPIENTS = [
  'person1@example.com',
  'person2@example.com',
  'person3@example.com',
  'person4@example.com',
];

/** Gmail label applied to threads that have already been forwarded. */
const PROCESSED_LABEL = 'Venmo-Forwarded';

/** How often (in minutes) to check for new emails: 1, 5, 10, 15 or 30. */
const CHECK_EVERY_MINUTES = 1;

/**
 * Only look at recent mail so the first run doesn't forward your whole
 * history. Widen (e.g. '30d') if you want older matches forwarded once.
 */
const LOOKBACK = '2d';

/** Words that must ALL appear (case-insensitive) in the subject or body. */
const REQUIRED_TERMS = ['paid you', 'venmo'];

// ---- Entry points ----------------------------------------------------------

/** Run once manually: validates config, creates the label and the trigger. */
function setup() {
  validateRecipients_();
  getOrCreateLabel_();

  // Remove old triggers for this function so re-running setup doesn't stack them.
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'forwardVenmoPayments')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('forwardVenmoPayments')
    .timeBased()
    .everyMinutes(CHECK_EVERY_MINUTES)
    .create();

  Logger.log('Setup complete. Checking every %s minutes.', CHECK_EVERY_MINUTES);
  forwardVenmoPayments(); // process anything already waiting
}

/** Run manually to stop the automation. */
function teardown() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'forwardVenmoPayments')
    .forEach(t => ScriptApp.deleteTrigger(t));
  Logger.log('Trigger removed. Emails will no longer be forwarded.');
}

/** Called by the time-based trigger. */
function forwardVenmoPayments() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10 * 1000)) return; // a previous run is still going

  try {
    validateRecipients_();
    const label = getOrCreateLabel_();
    const query =
      '"paid you" venmo newer_than:' + LOOKBACK +
      ' -label:' + PROCESSED_LABEL.replace(/\s+/g, '-') +
      ' -in:sent -in:spam -in:trash';

    const threads = GmailApp.search(query, 0, 50);
    const to = RECIPIENTS.join(',');
    let forwarded = 0;

    threads.forEach(thread => {
      thread.getMessages().forEach(message => {
        if (!matches_(message)) return;
        message.forward(to);
        forwarded++;
      });
      // Label even if nothing matched, so non-matching threads aren't rescanned.
      thread.addLabel(label);
    });

    if (forwarded) Logger.log('Forwarded %s email(s) to %s', forwarded, to);
  } finally {
    lock.releaseLock();
  }
}

// ---- Helpers ---------------------------------------------------------------

/** True if the message contains every REQUIRED_TERM (case-insensitive). */
function matches_(message) {
  const text = (message.getSubject() + '\n' + message.getPlainBody()).toLowerCase();
  return REQUIRED_TERMS.every(term => text.includes(term.toLowerCase()));
}

function getOrCreateLabel_() {
  return GmailApp.getUserLabelByName(PROCESSED_LABEL) ||
         GmailApp.createLabel(PROCESSED_LABEL);
}

function validateRecipients_() {
  const bad = RECIPIENTS.filter(r => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(r) ||
                                     /@example\.com$/i.test(r));
  if (bad.length) {
    throw new Error('Edit RECIPIENTS at the top of Code.gs first. Invalid: ' + bad.join(', '));
  }
}
